"""Greek SmPC (ΠΧΠ) and package leaflet (ΦΟΧ) PDFs from the EOF product search.

services.eof.gr/human-search is a JSF (PrimeFaces) application. Its robots.txt says
"Disallow: /"; DECISIONS Δ-18 allows a very slow crawl of it for the SmPC and leaflet
of nationally authorised medicines, under rules this module enforces and that must not
be relaxed:
  - one request at a time, >= 10 s (+ 0-5 s jitter) between ANY two requests, pages,
    AJAX postbacks and PDFs alike, also across runs (data/eof/requests.jsonl);
  - only between 00:00 and 07:00 Europe/Athens; outside it the crawler refuses to run,
    unless --now-for-testing N, which allows at most N requests;
  - an honest User-Agent naming the project; no browser impersonation, no proxies;
  - stop at once, exit non-zero, never retry on HTTP 401/403/429/503, other 5xx, a
    connection error, a CAPTCHA or a WAF page;
  - everything cached (raw responses and PDFs, sha256 + retrieved_at in the manifest);
    a product in the manifest is not searched again and a document URL is never
    fetched twice;
  - a file named STOP in the output directory blocks every run (kill switch).

How the application works (explored 14/11/2026):
  home.xhtml       search form "frmSearch" + javax.faces.ViewState (one per session).
  search           AJAX postback to home.xhtml (source frmSearch:btnSearch, render
                   frmMain) with frmSearch:txtDrugid = the 7-digit product code, i.e. the
                   first 7 digits of our 9-digit EOF package code (the last 2 are the
                   package). Answer: <partial-response> with the results table, one row
                   per product: code, name, status, licence, procedure, procedure number,
                   link view.xhtml?id=<64 hex> (the id changes per session).
  view.xhtml?id=   the product page: general data, MAH, packages (barcodes, each with our
                   EOF code at positions 3-11), ATC, and "Τεκμηρίωση" with the documents:
                   ./download?id=<32 hex>&type=<32 hex>, title SPC or PL, link text the
                   file name SPC_<code>_<version>.pdf. No document date is shown.
  download?id=     the file (application/octet-stream, Content-Disposition file name).
                   Same URL in every session, no cookie needed. Mostly PDF; older
                   documents are Word files (SPC_1899601_2.doc). The format is read from
                   the first bytes; pdf, doc, docx and rtf are kept with their extension,
                   anything else as .bin and reported, an HTML page stops the crawl.
                   Some products have no file at all (1898705).
Per product: 1 search + 1 page + up to 2 PDFs; plus 1 home page per session.

Order: products (7-digit code) with the most packages in our price list first; products
already linked to the EMA (data/current/ema_links.csv) are skipped, their documents
come from the EMA (ingest.ema, ingest.ema_pi).

Usage:
    python -m ingest.eof crawl --packages data/current/packages.csv --out data/eof
    python -m ingest.eof crawl ... --limit 50              # at most 50 products
    python -m ingest.eof crawl ... --now-for-testing 12    # outside the window, 12 requests
    python -m ingest.eof links --manifest data/eof/manifest.json --out data/current/eof_docs.csv

Exit status: 0 done (or window closed / testing budget used up), 2 refused to start
(outside the window, STOP file, another run), 3 blocked or the site failed (stop, do not
insist), 4 a page did not look as expected (fix the parser before the next run).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import requests

USER_AGENT = "farmaka-info/0.1 (+https://github.com/ppapadeas/farmaka-info; open data project)"
MIN_INTERVAL = 10.0  # seconds between ANY two requests (pages, AJAX postbacks, PDFs)
JITTER = 5.0  # plus a random 0..JITTER seconds
WINDOW_HOURS = (0, 7)  # crawl only 00:00-07:00 Europe/Athens
ATHENS = "Europe/Athens"
TIMEOUT = 60
LOCK_STALE_SECONDS = 600  # a lock not touched for this long belongs to a dead run

SEARCH_HOST = "services.eof.gr"
BASE = f"https://{SEARCH_HOST}/human-search/"
HOME = BASE + "home.xhtml"
SOURCE_NAME = "ΕΟΦ, αναζήτηση φαρμάκων ανθρώπινης χρήσης"

DOC_TYPES = ("smpc", "pil")  # the only documents we download
LINK_FIELDS = ("eof_code", "doc_type", "url", "date", "local_path")

# Documentation labels of the product page -> document type. "par" and "edu" are
# recorded, not downloaded; an unknown label is recorded as "other" and reported.
_DOC_LABELS = {
    "Περίληψη Χαρακτηριστικών Προϊόντος": "smpc",
    "Φύλλο Οδηγιών για το Χρήστη": "pil",
    "Δημόσια Έκθεση Αξιολόγησης": "par",
}
_DOC_TITLES = {"SPC": "smpc", "PL": "pil"}
_RESULT_COLUMNS = [
    "Κωδικός",
    "Ονομασία / Περιεκτικότητα",
    "Κατάσταση Α.Κ.",
    "Αρ. άδειας",
    "Διαδικασία",
    "Αρ. διαδικασίας",
    "",
]
_DOC_FILE = re.compile(r"^(SPC|PL)_(\d{7})_(\d+)\.(pdf|docx?|rtf)$", re.I)
# Formats the EOF serves documents in (older ones are Word files: SPC_1899601_2.doc).
DOC_FORMATS = ("pdf", "doc", "docx", "rtf")
_BARCODE = re.compile(r"^\d{13}$")
_ATC = re.compile(r"\b[A-Z]\d\d[A-Z]{2}\d\d\b")
_CODE7 = re.compile(r"^\d{7}$")

# Signs of being refused, in the body of an HTML answer: a WAF or challenge page.
_BLOCK_SIGNS = re.compile(
    r"cf-challenge|cf_chl_|access denied|request rejected|requested url was rejected"
    r"|too many requests|rate limit exceeded|temporarily blocked|bot detection|are you a robot",
    re.I,
)
# A CAPTCHA: counts on every page of the search application, and on any small page
# (challenge pages are small; the www.eof.gr home page carries a form widget).
_CAPTCHA = re.compile(r"captcha", re.I)
SMALL_PAGE = 30_000


# --- errors ------------------------------------------------------------------------


class Stop(Exception):
    """The crawl must stop now; never retried."""

    def __init__(self, msg: str, response: requests.Response | None = None):
        super().__init__(msg)
        self.response = response


class Blocked(Stop):
    """401/403/429/503, a CAPTCHA, a WAF page, a dropped connection."""


class OutsideWindow(Stop):
    """Not between 00:00 and 07:00 Athens time, and no testing budget."""


class BudgetExhausted(Stop):
    """--now-for-testing N: the N requests are used up."""


class EofFormatError(ValueError):
    """A page does not look like the one this module was written for."""


class ViewExpired(Exception):
    """The JSF view/session expired; a new session is needed."""


# --- request policy ----------------------------------------------------------------


def athens_now() -> datetime:
    return datetime.now(ZoneInfo(ATHENS))


def in_window(now: datetime) -> bool:
    start, end = WINDOW_HOURS
    return start <= now.astimezone(ZoneInfo(ATHENS)).hour < end


@dataclass
class Policy:
    """When a request may be sent. Clocks, sleep and randomness are injectable."""

    testing_budget: int | None = None  # --now-for-testing N; None = only inside the window
    any_time: bool = False  # --any-time: owner's decision (Δ-18 note, 15/11/2026); pacing unchanged
    deadline: float | None = None  # --max-minutes: stop cleanly at this wall time
    interval: float = MIN_INTERVAL
    jitter: float = JITTER
    clock: Callable[[], float] = time.monotonic
    wall: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep
    now: Callable[[], datetime] = athens_now
    rng: random.Random = field(default_factory=random.Random)
    last_wall: float | None = None  # previous request of an earlier run (request log)
    sent: int = 0
    _last: float = float("-inf")

    def check_allowed(self) -> None:
        if self.deadline is not None and self.wall() >= self.deadline:
            raise BudgetExhausted("--max-minutes: time budget used up")
        if self.testing_budget is not None:
            if self.sent >= self.testing_budget:
                raise BudgetExhausted(f"--now-for-testing {self.testing_budget}: budget used up")
            return
        if self.any_time:
            return
        now = self.now()
        if not in_window(now):
            raise OutsideWindow(
                f"{now:%H:%M} Athens time: crawling only between {WINDOW_HOURS[0]:02d}:00 and "
                f"{WINDOW_HOURS[1]:02d}:00 (or --now-for-testing N)"
            )

    def wait_turn(self) -> None:
        """Sleep until interval + jitter have passed since the previous request."""
        gap = self.interval + self.rng.uniform(0, self.jitter)
        wait = gap - (self.clock() - self._last)
        if self.last_wall is not None:
            wait = max(wait, gap - (self.wall() - self.last_wall))
        if wait > 0:
            self.sleep(wait)

    def mark_sent(self) -> None:
        self._last = self.clock()
        self.last_wall = self.wall()
        self.sent += 1


def check_response(r: requests.Response) -> None:
    """Raise Blocked on 401/403/429/503 or a CAPTCHA/WAF page; Stop on other 5xx."""
    if r.status_code in (401, 403, 429, 503):
        raise Blocked(f"HTTP {r.status_code} for {r.url}", r)
    if r.status_code >= 500:
        raise Stop(f"HTTP {r.status_code} for {r.url}", r)
    ctype = r.headers.get("Content-Type", "")
    if "html" in ctype or "xml" in ctype or "text" in ctype:
        text = r.content.decode("utf-8", "replace")
        m = _BLOCK_SIGNS.search(text[:50_000])
        if m:
            raise Blocked(f"blocking sign {m.group(0)!r} in {r.url}", r)
        host = urlparse(r.url or "").hostname or ""
        if (host == SEARCH_HOST or len(r.content) < SMALL_PAGE) and _CAPTCHA.search(text):
            raise Blocked(f"CAPTCHA in {r.url}", r)


class Client:
    """One request at a time, paced by Policy, honest User-Agent, no retries.

    Redirects are followed one hop at a time, each hop paced like any request. Every
    request is appended to `log` (JSON lines); its last line paces the next run.
    """

    def __init__(
        self,
        policy: Policy,
        log: Path | None = None,
        session: requests.Session | None = None,
        on_request: Callable[[], None] | None = None,
    ):
        self.policy = policy
        self.log = log
        self.on_request = on_request
        self.s = session or requests.Session()
        self.s.headers["User-Agent"] = USER_AGENT
        self.s.headers["Accept-Language"] = "el,en;q=0.5"
        if log is not None and policy.last_wall is None:
            policy.last_wall = _last_logged(log)

    def request(self, method: str, url: str, **kw) -> requests.Response:
        for _hop in range(5):
            r = self._one(method, url, **kw)
            if r.status_code in (301, 302, 303, 307, 308) and "Location" in r.headers:
                url = urljoin(r.url or url, r.headers["Location"])
                if r.status_code not in (307, 308):
                    method, kw = "GET", {k: v for k, v in kw.items() if k not in ("data", "json")}
                continue
            return r
        raise Stop(f"too many redirects from {url}")

    def _one(self, method: str, url: str, **kw) -> requests.Response:
        self.policy.check_allowed()
        self.policy.wait_turn()
        self.policy.check_allowed()  # the window may have closed while waiting
        if self.on_request:
            self.on_request()
        try:
            r = self.s.request(method, url, timeout=TIMEOUT, allow_redirects=False, **kw)
        except requests.RequestException as e:
            self.policy.mark_sent()
            self._write_log(method, url, f"error {type(e).__name__}", 0)
            raise Blocked(f"{type(e).__name__} for {url}: the site may be refusing us") from e
        self.policy.mark_sent()
        self._write_log(method, url, r.status_code, len(r.content))
        check_response(r)
        return r

    def _write_log(self, method: str, url: str, status: object, size: int) -> None:
        if self.log is None:
            return
        self.log.parent.mkdir(parents=True, exist_ok=True)
        rec = {"t": self.policy.wall(), "at": _utc_now(), "method": method, "url": url}
        rec |= {"status": status, "bytes": size}
        with self.log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _last_logged(log: Path) -> float | None:
    if not log.exists():
        return None
    lines = log.read_text(encoding="utf-8").splitlines()
    return float(json.loads(lines[-1])["t"]) if lines else None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# --- a small HTML tree (standard library only) -------------------------------------

_VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())


class Node:
    __slots__ = ("tag", "attrs", "children")

    def __init__(self, tag: str, attrs: dict[str, str]):
        self.tag = tag
        self.attrs = attrs
        self.children: list[Node | str] = []

    def iter(self) -> Iterable[Node]:
        yield self
        for c in self.children:
            if isinstance(c, Node):
                yield from c.iter()

    def find_all(self, tag: str | None = None, cls: str | None = None, **attrs: str) -> list[Node]:
        out = []
        for n in self.iter():
            if tag and n.tag != tag:
                continue
            if cls and cls not in n.attrs.get("class", "").split():
                continue
            if any(n.attrs.get(k.rstrip("_")) != v for k, v in attrs.items()):
                continue
            out.append(n)
        return out

    def find(self, tag: str | None = None, cls: str | None = None, **attrs: str) -> Node | None:
        found = self.find_all(tag, cls, **attrs)
        return found[0] if found else None

    def text(self) -> str:
        parts: list[str] = []

        def walk(n: Node) -> None:
            for c in n.children:
                if isinstance(c, str):
                    parts.append(c)
                elif c.tag not in ("script", "style"):
                    walk(c)

        walk(self)
        return " ".join("".join(parts).split())


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {})
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, {k: v or "" for k, v in attrs})
        self.stack[-1].children.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.stack[-1].children.append(Node(tag, {k: v or "" for k, v in attrs}))

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def parse_html(text: str) -> Node:
    b = _TreeBuilder()
    b.feed(text)
    b.close()
    return b.root


# --- pages -------------------------------------------------------------------------


@dataclass(frozen=True)
class SearchRow:
    code: str  # 7-digit product code
    name: str
    status: str
    licence: str
    procedure: str
    procedure_number: str
    view_url: str


@dataclass(frozen=True)
class SearchResult:
    rows: list[SearchRow]
    row_count: int  # total rows reported by the table (all pages)
    viewstate: str


@dataclass(frozen=True)
class DocLink:
    doc_type: str  # smpc | pil | par | edu | other
    label: str
    url: str
    filename: str

    @property
    def version(self) -> str:
        return doc_version(self.filename)


def doc_version(filename: str) -> str:
    """'SPC_0000902_2.pdf' -> '2' (the only version information the EOF shows)."""
    m = _DOC_FILE.match(filename.strip())
    return m.group(3) if m else ""


def sniff(data: bytes) -> str:
    """File format from the first bytes: pdf, doc, docx, rtf, html or unknown."""
    head = data[:512].lstrip()
    if data.startswith(b"%PDF"):
        return "pdf"
    if data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):  # OLE2: Word 97-2003
        return "doc"
    if data.startswith(b"PK\x03\x04") and b"word/" in data[:4096]:
        return "docx"
    if data.startswith(b"{\\rtf"):
        return "rtf"
    if head[:1] == b"<" and re.match(rb"<(?:!doctype|html|\?xml|head|body)", head, re.I):
        return "html"
    return "unknown"


@dataclass
class ProductPage:
    code: str
    name: str
    general: dict[str, str]
    mah: str
    atc: str
    barcodes: list[str]
    documents: list[DocLink]

    @property
    def eof_codes(self) -> list[str]:
        """Our 9-digit EOF codes, from the barcodes (EAN-13, prefix 280, code at 3-11)."""
        return sorted({b[3:12] for b in self.barcodes if b.startswith("280") and ean13_ok(b)})


def ean13_ok(code: str) -> bool:
    if not _BARCODE.match(code):
        return False
    digits = [int(c) for c in code]
    check = (10 - sum(d * (3 if i % 2 else 1) for i, d in enumerate(digits[:12])) % 10) % 10
    return check == digits[12]


def search_fields(home_html: str) -> dict[str, str]:
    """The fields a browser sends with the search form, as on the home page.

    Read from the page, not hard-coded, so renumbered component ids (j_idtNN) do not
    break the search. Text inputs as they are (empty), a select by its selected option
    (else its first), multi-selects and unchecked checkboxes not at all.
    """
    root = parse_html(home_html)
    form = root.find("form", id="frmSearch")
    if form is None:
        raise EofFormatError("home page: no form frmSearch")
    fields: dict[str, str] = {}
    for n in form.iter():
        name = n.attrs.get("name", "")
        if not name:
            continue
        if n.tag == "input":
            kind = n.attrs.get("type", "text")
            if kind in ("checkbox", "radio"):
                if "checked" in n.attrs:
                    fields[name] = n.attrs.get("value", "on")
            elif kind not in ("submit", "button", "image", "file"):
                fields[name] = n.attrs.get("value", "")
        elif n.tag == "select" and "multiple" not in n.attrs:
            opts = n.find_all("option")
            chosen = next((o for o in opts if "selected" in o.attrs), opts[0] if opts else None)
            fields[name] = chosen.attrs.get("value", "") if chosen else ""
        elif n.tag == "textarea":
            fields[name] = n.text()
    for need in ("frmSearch:txtDrugid", "javax.faces.ViewState"):
        if need not in fields:
            raise EofFormatError(f"home page: search form has no {need}")
    if not root.find("button", id="frmSearch:btnSearch"):
        raise EofFormatError("home page: no search button frmSearch:btnSearch")
    return fields


def search_request(fields: Mapping[str, str], code: str) -> dict[str, str]:
    """The AJAX postback of the search button (PrimeFaces: execute frmSearch, render frmMain)."""
    if not _CODE7.match(code):
        raise ValueError(f"not a 7-digit product code: {code!r}")
    data = {
        "javax.faces.partial.ajax": "true",
        "javax.faces.source": "frmSearch:btnSearch",
        "javax.faces.partial.execute": "frmSearch",
        "javax.faces.partial.render": "frmMain",
        "frmSearch:btnSearch": "frmSearch:btnSearch",
    }
    return data | dict(fields) | {"frmSearch:txtDrugid": code}


AJAX_HEADERS = {
    "Faces-Request": "partial/ajax",
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
}


def parse_search(xml_text: str, base: str = HOME) -> SearchResult:
    """Rows of the results table in the partial response of a search."""
    try:
        root = ET.fromstring(xml_text.encode("utf-8"))
    except ET.ParseError as e:
        raise EofFormatError(f"search: not a partial response ({e})") from e
    if root.tag != "partial-response":
        raise EofFormatError(f"search: root element {root.tag!r}")
    err = root.find("error")
    if err is not None:
        name = (err.findtext("error-name") or "").strip()
        if "ViewExpired" in name:
            raise ViewExpired(name)
        raise EofFormatError(f"search: server error {name!r}: {(err.findtext('error-message') or '')[:200]}")
    if root.find("redirect") is not None:
        raise ViewExpired("redirect in partial response")
    updates = {u.get("id", ""): u.text or "" for u in root.iter("update")}
    if "frmMain" not in updates:
        raise EofFormatError("search: no update of frmMain")
    viewstate = next((v.strip() for k, v in updates.items() if "javax.faces.ViewState" in k), "")
    table = parse_html(updates["frmMain"])
    head = table.find("thead", id="frmMain:tblResults_head")
    body = table.find("tbody", id="frmMain:tblResults_data")
    if head is None or body is None:
        raise EofFormatError("search: no results table")
    columns = [th.text() for th in head.find_all("th")]
    if columns != _RESULT_COLUMNS:
        raise EofFormatError(f"search: unknown result columns {columns}")
    rows = []
    for tr in body.find_all("tr"):
        if "data-ri" not in tr.attrs:
            continue  # "Δε βρέθηκαν εγγραφές."
        cells = tr.find_all("td")
        if len(cells) != len(_RESULT_COLUMNS):
            raise EofFormatError(f"search: row with {len(cells)} cells")
        link = cells[-1].find("a")
        href = link.attrs.get("href", "") if link else ""
        if not href.startswith("view.xhtml?id="):
            raise EofFormatError(f"search: unexpected product link {href!r}")
        t = [c.text() for c in cells]
        rows.append(SearchRow(t[0], t[1], t[2], t[3], t[4], t[5], urljoin(base, href)))
    m = re.search(r"rowCount:(\d+)", updates["frmMain"])
    return SearchResult(rows, int(m.group(1)) if m else len(rows), viewstate)


def _cards(root: Node) -> dict[str, Node]:
    out: dict[str, Node] = {}
    for card in root.find_all("div", cls="card"):
        title = card.find("div", cls="text-2xl")
        if title is not None:
            out.setdefault(title.text(), card)
    return out


def _pairs(card: Node) -> list[tuple[str, list[Node]]]:
    """(label, value divs) of each list item of a card."""
    out = []
    for li in card.find_all("li"):
        divs = [c for c in li.children if isinstance(c, Node) and c.tag == "div"]
        if divs:
            out.append((divs[0].text(), divs[1:]))
    return out


def parse_product(html_text: str, url: str = BASE) -> ProductPage:
    root = parse_html(html_text)
    data = root.find(id="productData")
    if data is None:
        raise EofFormatError("product page: no productData")
    cards = _cards(data)
    if "Γενικά" not in cards:
        raise EofFormatError(f"product page: no 'Γενικά' card (cards: {sorted(cards)})")
    general: dict[str, str] = {}
    mah = ""
    for label, values in _pairs(cards["Γενικά"]):
        value = values[0].text() if values else ""
        general[label] = value
        if label == "Κάτοχος Αδείας Κυκλοφορίας":
            mah = value
    code, name = general.get("Κωδικός", ""), general.get("Ονομασία", "")
    if not _CODE7.match(code) or not name:
        raise EofFormatError(f"product page: code {code!r}, name {name!r}")
    barcodes = []
    if "Συσκευασίες" in cards:
        for label, _values in _pairs(cards["Συσκευασίες"]):
            b = label.split()[0] if label else ""
            if _BARCODE.match(b):
                barcodes.append(b)
    atc = ""
    if "Ταξινόμηση ATC" in cards:
        m = _ATC.search(cards["Ταξινόμηση ATC"].text())
        atc = m.group(0) if m else ""
    documents: list[DocLink] = []
    doc_card = data.find(id="documentation") or cards.get("Τεκμηρίωση")
    if doc_card is None:
        raise EofFormatError("product page: no documentation card")
    for label, values in _pairs(doc_card):
        for v in values:
            for a in v.find_all("a"):
                href = a.attrs.get("href", "")
                if not href:
                    continue
                kind = _DOC_LABELS.get(label) or _DOC_TITLES.get(a.attrs.get("title", ""), "other")
                documents.append(DocLink(kind, label, urljoin(url, href), a.text()))
    edu = cards.get("Εκπαιδευτικό υλικό")
    if edu is not None:
        for a in edu.find_all("a"):
            if a.attrs.get("href"):
                documents.append(
                    DocLink("edu", "Εκπαιδευτικό υλικό", urljoin(url, a.attrs["href"]), a.text())
                )
    return ProductPage(code, name, general, mah, atc, barcodes, documents)


# --- what to crawl -----------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    code: str  # 7-digit product code
    eof_codes: tuple[str, ...]  # our packages of this product
    name: str  # first package name in our list


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def plan(packages: Iterable[Mapping[str, str]], ema_codes: set[str]) -> list[Target]:
    """Products of our price list not linked to the EMA, most packages first, then by code."""
    groups: dict[str, list[Mapping[str, str]]] = {}
    for p in packages:
        code = p["eof_code"]
        if not re.fullmatch(r"\d{9}", code):
            raise ValueError(f"not a 9-digit EOF code: {code!r}")
        groups.setdefault(code[:7], []).append(p)
    targets = [
        Target(k, tuple(sorted(p["eof_code"] for p in v)), v[0].get("name", ""))
        for k, v in groups.items()
        if not any(p["eof_code"] in ema_codes for p in v)
    ]
    return sorted(targets, key=lambda t: (-len(t.eof_codes), t.code))


# --- manifest ----------------------------------------------------------------------


def read_manifest(path: Path) -> dict:
    if path.exists():
        m = json.loads(path.read_text(encoding="utf-8"))
        m.setdefault("products", {})
        m.setdefault("documents", {})
        return m
    return {"source": BASE, "source_name": SOURCE_NAME, "products": {}, "documents": {}}


def write_manifest(path: Path, manifest: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest["updated_at"] = _utc_now()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_filename(name: str, fallback: str) -> str:
    name = name.strip()
    return (
        name if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,120}\.(?:pdf|docx?|rtf)", name, re.I) else fallback
    )


def _pdf_date(value: object) -> str:
    if isinstance(value, bytes):
        value = value.decode("latin-1", "replace")
    m = re.match(r"(?:D:)?(\d{4})(\d{2})(\d{2})", str(value or ""))
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


def pdf_dates(path: Path) -> tuple[str, str]:
    """(creation, modification) date of a PDF from its metadata, '' when unknown."""
    try:
        import pdfplumber
    except ImportError:
        return "", ""
    try:
        with pdfplumber.open(path) as pdf:
            md = pdf.metadata or {}
    except Exception:  # a PDF we cannot read keeps its bytes; dates stay unknown
        return "", ""
    return _pdf_date(md.get("CreationDate")), _pdf_date(md.get("ModDate"))


def _save(out: Path, rel: str, data: bytes) -> str:
    path = out / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and _sha256(path.read_bytes()) != _sha256(data):
        stem, ext = rel.rsplit(".", 1)
        rel = f"{stem}_{_sha256(data)[:12]}.{ext}"
        path = out / rel
    part = path.with_name(path.name + ".part")
    part.write_bytes(data)
    os.replace(part, path)
    return rel.replace(os.sep, "/")


# --- crawl -------------------------------------------------------------------------


class Lock:
    """One crawler at a time per output directory; a lock untouched for 10 min is stale."""

    def __init__(self, path: Path):
        self.path = path

    def __enter__(self) -> Lock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and time.time() - self.path.stat().st_mtime > LOCK_STALE_SECONDS:
            self.path.unlink()
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise RuntimeError(f"another crawl is running ({self.path} exists)") from None
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return self

    def touch(self) -> None:
        os.utime(self.path)

    def __exit__(self, *exc: object) -> None:
        self.path.unlink(missing_ok=True)


class Session:
    """A JSF session of the search application: home page once, then AJAX searches."""

    def __init__(self, client: Client):
        self.client = client
        self.fields: dict[str, str] | None = None

    def start(self) -> None:
        r = self.client.request("GET", HOME)
        if r.status_code != 200:
            raise Stop(f"home page: HTTP {r.status_code}", r)
        self.fields = search_fields(r.content.decode("utf-8"))

    def search(self, code: str) -> tuple[SearchResult, requests.Response]:
        for attempt in (1, 2):
            if self.fields is None:
                self.start()
            assert self.fields is not None
            r = self.client.request(
                "POST", HOME, data=search_request(self.fields, code), headers=AJAX_HEADERS
            )
            if r.status_code != 200:
                raise Stop(f"search {code}: HTTP {r.status_code}", r)
            try:
                result = parse_search(r.content.decode("utf-8"))
            except ViewExpired:
                if attempt == 2:
                    raise Stop(f"search {code}: session expired twice in a row", r) from None
                self.fields = None  # one new session, then give up
                continue
            if result.viewstate:
                self.fields["javax.faces.ViewState"] = result.viewstate
            return result, r
        raise AssertionError("unreachable")


@dataclass
class CrawlStats:
    products_done: int = 0
    not_found: int = 0
    documents_fetched: int = 0
    documents_reused: int = 0
    unknown_formats: list[str] = field(default_factory=list)
    stopped: str = ""


def _response_entry(kind: str, url: str, rel: str, data: bytes, at: str) -> dict:
    return {
        "kind": kind,
        "url": url,
        "path": rel,
        "sha256": _sha256(data),
        "bytes": len(data),
        "retrieved_at": at,
    }


def crawl_product(
    target: Target, session: Session, client: Client, out: Path, manifest: dict, stats: CrawlStats
) -> None:
    """Search, read the product page, download its SmPC and leaflet. The manifest is
    written after each step, so an interrupted product resumes without refetching."""
    mpath = out / "manifest.json"
    entry = manifest["products"].get(target.code)
    if entry is None:
        result, r = session.search(target.code)
        at = _utc_now()
        rel = _save(out, f"raw/search_{target.code}.xml", r.content)
        responses = [_response_entry("search", HOME, rel, r.content, at)]
        rows = [row for row in result.rows if row.code == target.code]
        if not rows:
            manifest["products"][target.code] = {
                "product_code": target.code,
                "state": "not_found",
                "price_list_name": target.name,
                "eof_codes": list(target.eof_codes),
                "search_rows": result.row_count,
                "responses": responses,
                "checked_at": at,
            }
            write_manifest(mpath, manifest)
            stats.not_found += 1
            return
        row = rows[0]
        r = client.request("GET", row.view_url)
        if r.status_code != 200:
            raise Stop(f"product page {target.code}: HTTP {r.status_code}", r)
        at = _utc_now()
        rel = _save(out, f"raw/view_{target.code}.html", r.content)
        responses.append(_response_entry("view", row.view_url, rel, r.content, at))
        page = parse_product(r.content.decode("utf-8"), row.view_url)
        if page.code != target.code:
            raise EofFormatError(f"product page for {target.code} shows code {page.code}")
        on_page = page.eof_codes
        entry = {
            "product_code": page.code,
            "name": page.name,
            "state": "documents_pending",
            "eof_status": row.status,
            "procedure": row.procedure,
            "licence_number": row.licence,
            "procedure_number": row.procedure_number,
            "mah": page.mah,
            "atc": page.atc,
            "view_url": row.view_url,
            "price_list_name": target.name,
            "eof_codes": list(target.eof_codes),
            "eof_codes_on_page": on_page,
            "eof_codes_not_on_page": sorted(set(target.eof_codes) - set(on_page)),
            "documents": [
                {"type": d.doc_type, "url": d.url, "filename": d.filename, "version": d.version}
                for d in page.documents
                if d.doc_type in DOC_TYPES
            ],
            "other_documents": [
                {"type": d.doc_type, "label": d.label, "url": d.url, "filename": d.filename}
                for d in page.documents
                if d.doc_type not in DOC_TYPES
            ],
            "responses": responses,
            "checked_at": at,
        }
        manifest["products"][target.code] = entry
        write_manifest(mpath, manifest)
    if entry.get("state") != "documents_pending":
        return
    for doc in entry["documents"]:
        url = doc["url"]
        if url in manifest["documents"]:
            stats.documents_reused += 1
            continue
        if urlparse(url).hostname != SEARCH_HOST:
            doc["skipped"] = "not on services.eof.gr"
            continue
        r = client.request("GET", url)
        if r.status_code == 404:
            doc["missing"] = f"HTTP 404 at {_utc_now()}"
            continue
        if r.status_code != 200:
            raise Stop(f"document {url}: HTTP {r.status_code}", r)
        at, data = _utc_now(), r.content
        fmt = sniff(data)
        code = entry["product_code"]
        if fmt == "html":  # an error page instead of a file: keep it, stop, look at it
            rel = _save(out, f"raw/document_{code}_{doc['type']}_{_sha256(data)[:12]}.html", data)
            raise EofFormatError(f"document {url} is an HTML page (kept as {rel})")
        ext = fmt if fmt in DOC_FORMATS else "bin"
        fallback = f"{doc['type']}_{code}_{_sha256(data)[:12]}.{ext}"
        name = _safe_filename(doc["filename"], fallback)
        if not name.lower().endswith("." + ext):
            name = fallback
        rel = _save(out, "pdf/" + name, data)
        created, modified = pdf_dates(out / rel) if fmt == "pdf" else ("", "")
        disposition = re.search(r'filename="?([^";]+)"?', r.headers.get("Content-Disposition", ""))
        manifest["documents"][url] = {
            "type": doc["type"],
            "format": fmt,
            "filename": doc["filename"],
            "served_filename": disposition.group(1) if disposition else "",
            "version": doc_version(doc["filename"]),
            "pdf_created": created,
            "pdf_modified": modified,
            "sha256": _sha256(data),
            "bytes": len(data),
            "retrieved_at": at,
            "path": rel,
        }
        write_manifest(mpath, manifest)
        stats.documents_fetched += 1
        if fmt not in DOC_FORMATS:
            stats.unknown_formats.append(url)
    entry["state"] = "done"
    write_manifest(mpath, manifest)
    stats.products_done += 1


def crawl(
    targets: list[Target], out: Path, client: Client, limit: int | None = None
) -> tuple[CrawlStats, dict]:
    """Crawl the targets not yet done in out/manifest.json (at most `limit`)."""
    manifest = read_manifest(out / "manifest.json")
    done = {k for k, v in manifest["products"].items() if v.get("state") in ("done", "not_found")}
    todo = [t for t in targets if t.code not in done]
    if limit is not None:
        todo = todo[:limit]
    stats = CrawlStats()
    session = Session(client)
    for t in todo:
        try:
            crawl_product(t, session, client, out, manifest, stats)
        except (OutsideWindow, BudgetExhausted) as e:
            stats.stopped = str(e)
            break
    return stats, manifest


# --- links -------------------------------------------------------------------------


def links(manifest: Mapping, prefix: str = "data/eof") -> list[dict[str, str]]:
    """One row per (our EOF code, downloaded document) of the products done."""
    rows = []
    for code7 in sorted(manifest.get("products", {})):
        p = manifest["products"][code7]
        if p.get("state") != "done":
            continue
        for d in p.get("documents", []):
            got = manifest.get("documents", {}).get(d["url"])
            if not got:
                continue
            for eof_code in p.get("eof_codes", []):
                rows.append(
                    {
                        "eof_code": eof_code,
                        "doc_type": d["type"],
                        "url": d["url"],
                        "date": got.get("pdf_modified") or got.get("pdf_created") or "",
                        "local_path": f"{prefix.rstrip('/')}/{got['path']}",
                    }
                )
    return sorted(rows, key=lambda r: (r["eof_code"], r["doc_type"], r["url"]))


def write_links(rows: Iterable[Mapping[str, str]], out: Path) -> int:
    out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=LINK_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in LINK_FIELDS})
            n += 1
    return n


# --- command line ------------------------------------------------------------------


def new_session() -> requests.Session:
    return requests.Session()


def _cmd_crawl(args: argparse.Namespace) -> int:
    out: Path = args.out
    if (out / "STOP").exists():
        print(f"refusing to run: {out / 'STOP'} exists (remove it to allow crawling again)")
        return 2
    policy = Policy(
        testing_budget=args.now_for_testing,
        now=athens_now,
        any_time=args.any_time,
        deadline=time.time() + args.max_minutes * 60 if args.max_minutes else None,
    )
    try:
        policy.check_allowed()
    except OutsideWindow as e:
        print(f"refusing to run: {e}")
        return 2
    ema = {r["eof_code"] for r in read_csv(args.ema_links)} if args.ema_links.exists() else set()
    if not args.ema_links.exists():
        print(f"warning: {args.ema_links} not found, no product is skipped as EMA")
    targets = plan(read_csv(args.packages), ema)
    try:
        lock = Lock(out / ".lock")
        with lock:
            client = Client(policy, log=out / "requests.jsonl", session=new_session(), on_request=lock.touch)
            try:
                stats, manifest = crawl(targets, out, client, args.limit)
            except Stop as e:
                print(f"STOPPED after {policy.sent} requests: {type(e).__name__}: {e}")
                print("do not run again before checking that services.eof.gr accepts us")
                return 3
            except EofFormatError as e:
                print(f"STOPPED after {policy.sent} requests: unexpected page: {e}")
                return 4
    except RuntimeError as e:
        print(f"refusing to run: {e}")
        return 2
    states = [v.get("state") for v in manifest["products"].values()]
    print(f"targets: {len(targets)} products (EMA ones skipped)")
    print(
        f"this run: {stats.products_done} products done, {stats.not_found} not found, "
        f"{stats.documents_fetched} documents fetched, {stats.documents_reused} reused, "
        f"{policy.sent} requests"
    )
    print(
        f"manifest: {states.count('done')} done, {states.count('not_found')} not found, "
        f"{states.count('documents_pending')} pending, {len(manifest['documents'])} documents"
    )
    no_docs = sum(1 for v in manifest["products"].values() if v.get("state") == "done" and not v["documents"])
    formats = [d.get("format", "pdf") for d in manifest["documents"].values()]
    print(f"products without SmPC/PIL files on the EOF page: {no_docs}")
    print("document formats: " + ", ".join(f"{f} {formats.count(f)}" for f in sorted(set(formats))))
    for url in stats.unknown_formats:
        print(f"  unknown format (kept as .bin): {url}")
    if stats.stopped:
        print(f"stopped: {stats.stopped}")
    return 0


def _cmd_links(args: argparse.Namespace) -> int:
    manifest = read_manifest(args.manifest)
    n = write_links(links(manifest, args.manifest.parent.as_posix()), args.out)
    print(f"{n} rows -> {args.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SmPC and leaflet PDFs from the EOF product search.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("crawl", help="download documents of products not yet in the manifest")
    c.add_argument("--packages", type=Path, default=Path("data/current/packages.csv"))
    c.add_argument("--ema-links", type=Path, default=Path("data/current/ema_links.csv"))
    c.add_argument("--out", type=Path, default=Path("data/eof"))
    c.add_argument("--limit", type=int, help="at most N products in this run")
    c.add_argument(
        "--now-for-testing",
        type=int,
        metavar="N",
        help="run outside 00:00-07:00 Athens time, at most N requests (keep N small)",
    )
    c.add_argument(
        "--any-time",
        action="store_true",
        help="ignore the 00:00-07:00 window (same >=10 s pacing, STOP file and 403/429 rules)",
    )
    c.add_argument("--max-minutes", type=int, help="stop cleanly after N minutes (CI job limits)")
    lk = sub.add_parser("links", help="write eof_docs.csv from the manifest")
    lk.add_argument("--manifest", type=Path, default=Path("data/eof/manifest.json"))
    lk.add_argument("--out", type=Path, default=Path("data/current/eof_docs.csv"))
    args = ap.parse_args(argv)
    if args.cmd == "crawl":
        if args.now_for_testing is not None and args.now_for_testing < 1:
            ap.error("--now-for-testing needs N >= 1")
        return _cmd_crawl(args)
    return _cmd_links(args)


if __name__ == "__main__":
    sys.exit(main())
