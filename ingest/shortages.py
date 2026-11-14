"""Medicines in shortage: the EOF lists and notices, and the EMA shortages catalogue.

Sources (explored 14/11/2026, see docs/DATA_SOURCES.md, S9):

  EOF, www.eof.gr, category "Επάρκεια φαρμάκων" (LISTING, ~10 posts per page, newest
  first). robots.txt allows everything except /wp-admin/. Kinds of post (classify()):
    limited_availability  "ΛΙΣΤΑ ΦΑΡΜΑΚΕΥΤΙΚΩΝ ΣΚΕΥΑΣΜΑΤΩΝ ΠΕΡΙΟΡΙΣΜΕΝΗΣ ΔΙΑΘΕΣΙΜΟΤΗΤΑΣ <date>",
                          monthly; one PDF made from Excel (text, ruled tables): barcode,
                          name, ATC, substance, dispensing, MAH, start, expected end,
                          reason, alternatives. A second section "…ΕΚΤΑΚΤΗΣ ΚΑΛΥΨΗΣ" lists
                          imports for emergency cover (Ι.Φ.Ε.Τ.), without alternatives.
    discontinuation_list  "ΛΙΣΤΑ … ΟΡΙΣΤΙΚΗΣ ΔΙΑΚΟΠΗΣ ΚΥΚΛΟΦΟΡΙΑΣ <date>", same kind of PDF.
    export_ban            "ΠΡΟΣΩΡΙΝΗ ΑΠΑΓΟΡΕΥΣΗ ΠΑΡΑΛΛΗΛΩΝ ΕΞΑΓΩΓΩΝ …", every ~3 months; a
                          scanned decision (no text layer) with the products in an annex.
                          Not on Diavgeia. A scanned file gives no rows: it is listed in
                          the report as pending, never guessed.
    discontinuation       notices of a planned discontinuation of one product (title names
                          brand and strength; the company letter is linked, not read).
    shortage              "Επικοινωνία για (την) έλλειψη …" notices about one product.
    other                 stock declarations, procedures, sales data: ignored.
  EMA, the shortages table of "Download medicine data" (EMA_SHORTAGES_XLSX): shortages of
  medicines assessed by EMA (mostly centrally authorised), EU level, no per-country data.

Only the current state goes to shortages.csv: the newest list of each kind, the notices of
the last NOTICE_DAYS days, and the EMA entries not marked resolved. Every post ever seen
in the listing is kept in the report (history).

Package codes: a barcode (EAN-13, prefix 280, valid check digit) carries our 9-digit EOF
code in positions 4-12. Rows without a valid barcode, and notices, are matched by name,
conservatively: the brand (words before the dose form, name_key()) AND the strength must
equal those of our packages, and they must point to one product (first 7 digits of the
code); otherwise no code, and the row is listed in the report. EMA entries are matched
through ema_links.csv (ingest.ema), restricted to the strengths EMA lists, if any.

Fetching is polite: one request at a time, >= MIN_INTERVAL s between any two (also across
runs, from requests.jsonl), an honest User-Agent, no retries: any answer other than 200
stops the run. Everything downloaded is kept with its sha256 in manifest.json; a file is
never downloaded twice, post pages only again when the listing says they changed, the
first listing page at most every LISTING_MAX_AGE hours. A file named STOP in the cache
directory blocks every run.

Usage:
    python -m ingest.shortages fetch --out data/shortages [--max-requests N] [--backfill]
    python -m ingest.shortages build --cache data/shortages \
        --packages data/current/packages.csv --otc data/current/otc_packages.csv --out data/current

Exit status: 0 done; 1 request budget used up (run again to continue); 2 refused to start
(STOP file); 3 refused or failed answer (stop, do not insist); 4 a page or file did not
look as expected (fix the parser).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import sys
import time
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlparse

import requests

from ingest.ema import name_key
from sitegen.drug import split_name, strength

USER_AGENT = "farmaka-info/0.1 (+https://github.com/ppapadeas/farmaka-info; open data project)"
MIN_INTERVAL = 5.0  # seconds between any two requests, to any host
TIMEOUT = 90
MAX_REDIRECTS = 3
LISTING_MAX_AGE = timedelta(hours=6)  # the first listing page is checked at most this often
EMA_MAX_AGE = timedelta(hours=20)  # the EMA table is regenerated every night

EOF = "https://www.eof.gr"
LISTING = f"{EOF}/category/farmaka/eparkeia-farmaka/"
EMA_SHORTAGES_XLSX = "https://www.ema.europa.eu/en/documents/report/medicines-output-shortages-report_en.xlsx"

LIST_KINDS = ("limited_availability", "discontinuation_list", "export_ban")
NOTICE_DAYS = {"discontinuation": 365, "shortage": 180}
LIST_TYPE = {
    "limited_availability": "limited_availability",
    "discontinuation_list": "discontinuation",
    "export_ban": "export_ban",
    "discontinuation": "discontinuation",
    "shortage": "shortage",
}
EMA_CURRENT = {"Shortage ongoing", "Medicine discontinued"}
EMA_STATUSES = EMA_CURRENT | {"Shortage resolved"}

FIELDS = (
    "eof_code",
    "list_type",
    "status",
    "published",
    "valid_until",
    "start",
    "name",
    "barcode",
    "section",
    "alternatives",
    "matched_by",
    "in_catalogue",
    "source_url",
    "source_file",
    "source_title",
)

_ATTACHMENT = re.compile(r"\.(pdf|xlsx|xls|docx?|csv)$", re.I)
_MAGIC = {
    "pdf": b"%PDF",
    "xlsx": b"PK",
    "docx": b"PK",
    "xls": b"\xd0\xcf\x11\xe0",
    "doc": b"\xd0\xcf\x11\xe0",
}


class ShortageError(RuntimeError):
    """A page or file does not look like the ones this module was written for (exit 4)."""


class UnknownHeader(ShortageError):
    pass


class Blocked(RuntimeError):
    """A refused or failed request: stop, do not retry (exit 3)."""


class BudgetExhausted(RuntimeError):
    """--max-requests reached; the next run continues (exit 1)."""


# --- text -------------------------------------------------------------------------


def _fold(text: str) -> str:
    """Uppercase without accents; Greek stays Greek (unlike ingest.ema._fold)."""
    t = unicodedata.normalize("NFKD", text).upper()
    return "".join(c for c in t if not unicodedata.combining(c))


def _clean(cell: object) -> str:
    return re.sub(r"\s+", " ", str(cell or "")).strip()


# Month stems, accents removed: full genitive ("ΟΚΤΩΒΡΙΟΥ", also "ΟΚΤΩΜΒΡΙΟΥ") and the
# short forms of the listing ("Νοέ", "Ιούλ").
_MONTHS = {
    "ΙΑΝ": 1,
    "ΦΕΒ": 2,
    "ΜΑΡ": 3,
    "ΑΠΡ": 4,
    "ΜΑΙ": 5,
    "ΙΟΥΝ": 6,
    "ΙΟΥΛ": 7,
    "ΑΥΓ": 8,
    "ΣΕΠ": 9,
    "ΟΚΤ": 10,
    "ΝΟΕ": 11,
    "ΔΕΚ": 12,
}
_GREEK_DATE = re.compile(r"\b(\d{1,2})\s+([Α-Ω]{3,})\b(?:\s+(\d{4}))?")
_DMY = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4}|\d{2})\b")


def _month(word: str) -> int | None:
    for stem, m in _MONTHS.items():
        if word.startswith(stem):
            return m
    return None


def greek_date(text: str, year: int | None = None) -> date | None:
    """'31 ΟΚΤΩΒΡΙΟΥ 2026', '10 Νοέ 2026', '28/03/2025'; '31 ΜΑΪΟΥ' needs `year`."""
    t = _fold(text)
    for m in _GREEK_DATE.finditer(t):
        month = _month(m.group(2))
        y = int(m.group(3)) if m.group(3) else year
        if month and y:
            return date(y, month, int(m.group(1)))
    m = _DMY.search(t)
    if m:
        return _dmy(m)
    return None


def _dmy(m: re.Match) -> date:
    y = int(m.group(3))
    return date(y + 2000 if y < 100 else y, int(m.group(2)), int(m.group(1)))


def iso_date(cell: object) -> str:
    """'22/10/26' or '22/10/2026' -> '2026-10-22'; empty stays empty; anything else is an error."""
    t = _clean(cell)
    if not t:
        return ""
    m = _DMY.fullmatch(t)
    if not m:
        raise ShortageError(f"not a date: {t!r}")
    return _dmy(m).isoformat()


# --- codes ------------------------------------------------------------------------


def ean13_ok(code: str) -> bool:
    if not re.fullmatch(r"\d{13}", code):
        return False
    s = sum(int(c) * (3 if i % 2 else 1) for i, c in enumerate(code[:12]))
    return (10 - s % 10) % 10 == int(code[12])


def eof_code_from_barcode(barcode: str) -> str | None:
    """'2801928501017' -> '192850101': positions 4-12 of a valid EAN-13 starting with 280."""
    b = re.sub(r"\s", "", barcode)
    if b.startswith("280") and ean13_ok(b):
        return b[3:12]
    return None


# --- EOF listing and posts --------------------------------------------------------


@dataclass(frozen=True)
class Post:
    post_id: int
    url: str
    title: str
    published: date  # as shown in the listing
    modified: str  # ISO date-time of the last change, from the listing
    kind: str

    @property
    def list_date(self) -> date:
        """The date the list refers to, from the title; else the publication date."""
        return greek_date(self.title, self.published.year) or self.published

    def key(self) -> tuple:
        return (self.list_date, self.published, self.post_id)


def classify(title: str) -> str:
    t = _fold(title)
    if "ΑΠΑΓΟΡΕΥΣΗ" in t and "ΕΞΑΓΩΓ" in t:
        return "export_ban"
    if "ΛΙΣΤΑ" in t and "ΠΕΡΙΟΡΙΣΜΕΝΗΣ ΔΙΑΘΕΣΙΜΟΤΗΤΑΣ" in t:
        return "limited_availability"
    if "ΛΙΣΤΑ" in t and "ΔΙΑΚΟΠΗΣ" in t:
        return "discontinuation_list"
    if "ΔΙΑΚΟΠΗ" in t:
        return "discontinuation"
    if "ΕΛΛΕΙΨ" in t:
        return "shortage"
    return "other"


_ARTICLE = re.compile(r"<article\b[^>]*\bid=\"post-(\d+)\"(.*?)</article>", re.S)
_ENTRY_TITLE = re.compile(r"<h3 class=\"entry-title\">\s*<a href=\"([^\"]+)\"[^>]*>(.*?)</a>", re.S)
_META_DATE = re.compile(r"class=\"meta-date[^\"]*\">([^<]+)<")
_MODIFIED = re.compile(r"<time[^>]*datetime=\"([^\"]+)\"")
_NEXT_PAGE = re.compile(r"href=[\"']([^\"']*/category/farmaka/eparkeia-farmaka/page/(\d+)/?)[\"']")


def _text(fragment: str) -> str:
    return _clean(html.unescape(re.sub(r"<[^>]+>", " ", fragment)))


def parse_listing(page: str) -> tuple[list[Post], list[int]]:
    """Posts of a category page, and the page numbers it links to."""
    posts = []
    for m in _ARTICLE.finditer(page):
        body = m.group(2)
        t, d, mod = _ENTRY_TITLE.search(body), _META_DATE.search(body), _MODIFIED.search(body)
        if not (t and d and mod):
            raise ShortageError(f"listing: post {m.group(1)} without title, date or time")
        published = greek_date(d.group(1))
        if not published:
            raise ShortageError(f"listing: post {m.group(1)}: date {d.group(1)!r}")
        title = _text(t.group(2))
        url = canonical_url(html.unescape(t.group(1)))
        posts.append(Post(int(m.group(1)), url, title, published, mod.group(1), classify(title)))
    pages = sorted({int(n) for _, n in _NEXT_PAGE.findall(page)})
    return posts, pages


def _div_end(page: str, start: int) -> int:
    """Index just after the </div> that closes the <div> opened at `start`."""
    depth = 0
    for m in re.compile(r"<div\b|</div>").finditer(page, start):
        depth += 1 if m.group(0).startswith("<div") else -1
        if depth == 0:
            return m.end()
    raise ShortageError("unclosed <div>")


def parse_post(page: str, url: str) -> tuple[str, list[str]]:
    """(body text, attachment URLs) of a post page."""
    i = page.find('<div class="entry-content')
    if i < 0:
        raise ShortageError(f"post without entry-content: {url}")
    body = page[i : _div_end(page, i)]
    body = re.sub(r"<script.*?</script>|<style.*?</style>", " ", body, flags=re.S)
    files: list[str] = []
    for href in re.findall(r"href=\"?([^\"\s>]+)", body):
        absolute = canonical_url(urljoin(url, html.unescape(href)))
        if "/wp-content/uploads/" in absolute and _ATTACHMENT.search(urlparse(absolute).path):
            if absolute not in files:
                files.append(absolute)
    return _text(body), files


def canonical_url(url: str) -> str:
    """One spelling per URL: path decoded, then encoded once (Greek file names)."""
    p = urlparse(url)
    return p._replace(path=quote(unquote(p.path), safe="/-_.~%")).geturl()


# --- cache and polite client ------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _stamp(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Cache:
    """Downloaded responses: cache/raw/<sha256[:12]>_<name>, cache/manifest.json.

    manifest.json maps each URL to its versions, oldest first; a new version is kept only
    when the bytes differ. Files are never overwritten.
    """

    def __init__(self, root: Path):
        self.root = root
        self.path = root / "manifest.json"
        self.entries: dict[str, list[dict]] = (
            json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        )

    def latest(self, url: str) -> dict | None:
        versions = self.entries.get(canonical_url(url))
        return versions[-1] if versions else None

    def file(self, url: str) -> Path | None:
        e = self.latest(url)
        return self.root / e["file"] if e else None

    def versions(self, prefix: str) -> list[tuple[str, dict]]:
        return [(u, v) for u, vs in self.entries.items() if u.startswith(prefix) for v in vs]

    def store(self, url: str, data: bytes, headers: Mapping[str, str], when: datetime) -> Path:
        url = canonical_url(url)
        digest = _sha256(data)
        versions = self.entries.setdefault(url, [])
        if versions and versions[-1]["sha256"] == digest:
            versions[-1]["checked_at"] = _stamp(when)
        else:
            name = unquote(urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]) or "index"
            name = re.sub(r"[^\w.-]+", "_", name)[-80:]
            raw = self.root / "raw"
            raw.mkdir(parents=True, exist_ok=True)
            path = raw / f"{digest[:12]}_{name}"
            path.write_bytes(data)
            versions.append(
                {
                    "file": path.relative_to(self.root).as_posix(),
                    "sha256": digest,
                    "bytes": len(data),
                    "content_type": headers.get("Content-Type", ""),
                    "last_modified": headers.get("Last-Modified", ""),
                    "retrieved_at": _stamp(when),
                    "checked_at": _stamp(when),
                }
            )
        self.path.write_text(json.dumps(self.entries, ensure_ascii=False, indent=1), encoding="utf-8")
        return self.root / versions[-1]["file"]


class Client:
    """One request at a time, MIN_INTERVAL s apart (also across runs), no retries."""

    def __init__(self, cache: Cache, max_requests: int, session: requests.Session | None = None):
        self.cache = cache
        self.max_requests = max_requests
        self.used = 0
        self.s = session or requests.Session()
        self.s.headers["User-Agent"] = USER_AGENT
        self.log = cache.root / "requests.jsonl"

    def _last(self) -> float:
        if not self.log.exists():
            return 0.0
        lines = self.log.read_text(encoding="utf-8").splitlines()
        return json.loads(lines[-1])["t"] if lines else 0.0

    def _request(self, url: str) -> requests.Response:
        if self.used >= self.max_requests:
            raise BudgetExhausted(f"request budget ({self.max_requests}) used up before {url}")
        wait = MIN_INTERVAL - (time.time() - self._last())
        if wait > 0:
            time.sleep(wait)
        t = time.time()
        self.used += 1
        try:
            r = self.s.get(url, timeout=TIMEOUT, allow_redirects=False)
        except requests.RequestException as e:
            self._write_log(t, url, None, 0)
            raise Blocked(f"{url}: {e}") from e
        self._write_log(t, url, r.status_code, len(r.content))
        return r

    def _write_log(self, t: float, url: str, status: int | None, size: int) -> None:
        self.log.parent.mkdir(parents=True, exist_ok=True)
        with self.log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": t, "url": url, "status": status, "bytes": size}) + "\n")

    def get(self, url: str, refresh: bool = False) -> Path:
        """The cached copy of `url`; downloads it when there is none or when `refresh`."""
        url = canonical_url(url)
        cached = self.cache.file(url)
        if cached and not refresh:
            return cached
        target = url
        for _ in range(MAX_REDIRECTS + 1):
            r = self._request(target)
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("Location"):
                nxt = urljoin(target, r.headers["Location"])
                if urlparse(nxt).netloc != urlparse(url).netloc:
                    raise Blocked(f"{url}: redirected to another host {nxt}")
                target = nxt
                continue
            if r.status_code != 200:
                raise Blocked(f"{target}: HTTP {r.status_code}")
            ext = urlparse(url).path.rsplit(".", 1)[-1].lower()
            if ext in _MAGIC and not r.content.startswith(_MAGIC[ext]):
                raise ShortageError(f"{url}: not a .{ext} file ({r.headers.get('Content-Type')})")
            return self.cache.store(url, r.content, r.headers, _now())
        raise Blocked(f"{url}: too many redirects")


# --- discovery (fetch) ------------------------------------------------------------


def known_posts(cache: Cache) -> dict[int, Post]:
    """Every post seen in any cached version of any listing page; the newest view wins."""
    seen: dict[int, tuple[str, Post]] = {}
    for _, v in cache.versions(LISTING):
        posts, _ = parse_listing((cache.root / v["file"]).read_text(encoding="utf-8"))
        for p in posts:
            if p.post_id not in seen or seen[p.post_id][0] <= v["retrieved_at"]:
                seen[p.post_id] = (v["retrieved_at"], p)
    return {k: p for k, (_, p) in seen.items()}


def _page_url(n: int) -> str:
    return LISTING if n == 1 else f"{LISTING}page/{n}/"


def _stale(entry: dict | None, max_age: timedelta, now: datetime) -> bool:
    if entry is None:
        return True
    checked = datetime.strptime(entry["checked_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return now - checked > max_age


def _post_changed(cache: Cache, post: Post) -> bool:
    """True when there is no copy of the post page or the listing shows a later change."""
    e = cache.latest(post.url)
    if e is None:
        return True
    modified = datetime.fromisoformat(post.modified).astimezone(UTC)
    checked = datetime.strptime(e["checked_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return modified > checked


def current_posts(posts: Iterable[Post], today: date) -> list[Post]:
    """The newest post of each list kind, and the notices within NOTICE_DAYS."""
    posts = list(posts)
    chosen = []
    for kind in LIST_KINDS:
        of_kind = sorted((p for p in posts if p.kind == kind), key=Post.key, reverse=True)
        chosen += of_kind[:1]
    for kind, days in NOTICE_DAYS.items():
        chosen += sorted(
            (p for p in posts if p.kind == kind and (today - p.published).days <= days),
            key=Post.key,
            reverse=True,
        )
    return chosen


def fetch(client: Client, today: date, backfill: bool = False, ema: bool = True) -> dict:
    cache = client.cache
    now = _now()
    before = known_posts(cache)
    # 1. the listing: page 1, then older pages while every post on a page is new
    #    (or all pages with --backfill), one request each
    n = 1
    while True:
        url = _page_url(n)
        refresh = n == 1 and _stale(cache.latest(url), LISTING_MAX_AGE, now)
        if not backfill and n > 1:
            refresh = True
        path = client.get(url, refresh=refresh)
        posts, pages = parse_listing(path.read_text(encoding="utf-8"))
        if not posts:
            raise ShortageError(f"no posts on {url}")
        all_new = not any(p.post_id in before for p in posts)
        if n + 1 not in pages or not (backfill or (all_new and before)):
            break
        n += 1
    # 2. the post pages and attachments of the current lists and notices
    for post in current_posts(known_posts(cache).values(), today):
        page = client.get(post.url, refresh=_post_changed(cache, post))
        if post.kind not in LIST_KINDS:
            continue  # notices: the title and text are enough
        _, files = parse_post(page.read_text(encoding="utf-8"), post.url)
        if not files:
            raise ShortageError(f"no attachment in list post {post.url}")
        for f in files:
            client.get(f)  # files are never downloaded twice
    # 3. the EMA table, at most once per EMA_MAX_AGE
    if ema:
        client.get(EMA_SHORTAGES_XLSX, refresh=_stale(cache.latest(EMA_SHORTAGES_XLSX), EMA_MAX_AGE, now))
    return {"requests": client.used, "posts_known": len(known_posts(cache))}


# --- EOF list files ---------------------------------------------------------------


def _header_key(text: str) -> str:
    return re.sub(r"[^A-ZΑ-Ω0-9]", "", _fold(text or ""))


# Header cells, normalised (_header_key), of the EOF lists -> field. Anything else is an
# error (UnknownHeader). The scanned export-ban annex has "Α/Α", "Barcode", "Περιγραφή
# Προϊόντος", "Κάτοχος άδειας κυκλοφορίας (ΚΑΚ)", "Κωδικός ATC", "Δραστική(ές) ουσία(ες)";
# they are here for the day the EOF publishes it with text.
_HEADERS = {
    "ΑΑ": "seq",
    "BARCODE": "barcode",
    "ΠΕΡΙΓΡΑΦΗ": "name",
    "ΠΕΡΙΓΡΑΦΗΠΡΟΙΟΝΤΟΣ": "name",
    "ATC": "atc",
    "ATCΤΑΞΙΝΟΜΗΣΗΜΗΣΕΙΣ": "atc",
    "ΚΩΔΙΚΟΣATC": "atc",
    "ΔΡΑΣΤΙΚΗΕΣΟΥΣΙΑΕΣ": "substance",
    "ΤΡΟΠΟΣΔΙΑΘΕΣΗΣ": "dispensing",
    "ΚΑΤΟΧΟΣΑΔΕΙΑΣΚΥΚΛΟΦΟΡΙΑΣ": "mah",
    "ΚΑΤΟΧΟΣΑΔΕΙΑΣΚΥΚΛΟΦΟΡΙΑΣΚΑΚ": "mah",
    "ΗΜΕΝΑΡΞΗΣΕΝΑΡΞΗΣ": "start",
    "ΗΜΕΝΑΡΞΗΣ": "start",
    "ΗΜΛΗΞΗΣΔΙΑΚΟΠΗΣ": "end",
    "ΗΜΛΗΞΗΣ": "end",
    "ΗΜΟΡΙΣΤΙΚΗΣΔΙΑΚΟΠΗΣ": "discontinued",
    "ΑΙΤΙΑΠΕΡΙΟΡΙΣΜΕΝΗΣΔΙΑΘΕΣΙΜΟΤΗΤΑΣ": "reason",
    "ΕΝΑΛΛΑΚΤΙΚΕΣΘΕΡΑΠΕΥΤΙΚΕΣΕΠΙΛΟΓΕΣ": "alternatives",
}
_REQUIRED = {"barcode", "name"}


@dataclass
class ListRow:
    section: str
    fields: dict[str, str]


class ScannedDocument(ShortageError):
    """A PDF without a text layer: nothing can be read from it without a transcription."""


def pdf_tables(path: Path) -> list[list[list[str | None]]]:
    import pdfplumber

    tables = []
    with pdfplumber.open(path) as pdf:
        if not any(p.chars for p in pdf.pages):
            raise ScannedDocument(f"{path.name}: {len(pdf.pages)} pages without text (scanned)")
        for page in pdf.pages:
            tables += page.extract_tables()
    return tables


def parse_tables(tables: Iterable[list[list[str | None]]]) -> list[ListRow]:
    """Rows of an EOF list from its tables, in order.

    A row with only its first cell filled is a section heading (the list title, repeated
    on every page, or a new section); a row whose first cell is "Barcode" is the header
    and must be known; every other row is a product of the current section.
    """
    rows: list[ListRow] = []
    section, header = "", None
    for table in tables:
        for raw in table:
            cells = [_clean(c) for c in raw]
            if not any(cells):
                continue
            if cells[0] and not any(cells[1:]) and all(c is None or c == "" for c in raw[1:]):
                if not re.fullmatch(r"\d{13}", cells[0]):
                    section = cells[0]
                    continue
            if _header_key(cells[0]) in ("BARCODE", "ΑΑ"):
                header = []
                for c in raw:
                    k = _header_key(c or "")
                    if k not in _HEADERS:
                        raise UnknownHeader(f"unknown column {c!r} in section {section!r}")
                    header.append(_HEADERS[k])
                if not _REQUIRED <= set(header):
                    raise UnknownHeader(f"section {section!r}: no barcode or name column: {raw}")
                continue
            if header is None:
                raise ShortageError(f"row before any header: {cells}")
            if len(cells) != len(header):
                raise ShortageError(f"{len(cells)} cells under a {len(header)}-column header: {cells}")
            rows.append(ListRow(section, dict(zip(header, cells, strict=True))))
    return rows


# --- matching by name -------------------------------------------------------------


def _amount(text: str) -> str:
    try:
        d = Decimal(text.replace(",", "."))
    except InvalidOperation:
        return text
    return format(d.normalize(), "f")


_UNITS = {"MG": "MG", "MCG": "MCG", "ΜG": "MCG", "G": "G", "IU": "IU", "U": "U", "ML": "ML", "%": "%"}
_TEXT_STRENGTH = re.compile(r"(\d+(?:[.,]\d+)?)\s*(MCG|ΜG|MG|IU|G|U|%)(?![A-ZΑ-Ω])")


def text_strengths(text: str) -> list[str]:
    """'284 mg', '500 mg, 1 g και 2 g' -> ['284MG'], ['500MG', '1G', '2G']."""
    t = _fold(text).replace("µ", "Μ")
    return [_amount(a) + _UNITS[u] for a, u in _TEXT_STRENGTH.findall(t)]


def strength_key(name: str) -> str | None:
    """The first amount+unit of a package name: '2MG/ML' -> '2MG', '(10+5)MG' -> '10+5MG'."""
    s = strength(name)
    if not s:
        return None
    first = s[0].split("/")[0]
    m = re.fullmatch(r"([\d.+]+)([A-Z%]+)", first)
    if not m:
        return first
    return "+".join(_amount(x) for x in m.group(1).split("+")) + m.group(2)


@dataclass
class Catalogue:
    """Our packages (price list and OTC list), indexed for matching."""

    names: dict[str, str] = field(default_factory=dict)  # eof_code -> name
    source: dict[str, str] = field(default_factory=dict)  # eof_code -> packages | otc
    by_brand: dict[str, list[tuple[str, str | None]]] = field(default_factory=dict)  # (code, strength)
    by_name: dict[str, list[str]] = field(default_factory=dict)  # folded full name -> codes
    substances: set[str] = field(default_factory=set)  # name_key of every substance (INN)

    def add(self, code: str, name: str, source: str, substances: str = "") -> None:
        if code in self.names:
            return  # the price list wins; ingest.otc reports codes in both
        self.names[code] = name
        self.source[code] = source
        brand = name_key(split_name(name)[0])
        if brand:
            self.by_brand.setdefault(brand, []).append((code, strength_key(name)))
        self.by_name.setdefault(name_key(name), []).append(code)
        self.substances |= {name_key(s) for s in substances.split("|") if s.strip()}

    def is_brand(self, key: str) -> bool:
        """A brand of our lists that is not also a substance name ('QUETIAPINE' is not a brand)."""
        return key in self.by_brand and key not in self.substances

    @classmethod
    def read(cls, packages: Path, otc: Path | None) -> Catalogue:
        cat = cls()
        for path, source in ((packages, "packages"), (otc, "otc")):
            if path is None:
                continue
            with path.open(encoding="utf-8", newline="") as fh:
                for r in csv.DictReader(fh):
                    cat.add(r["eof_code"], r["name"], source, r.get("substances", ""))
        return cat


@dataclass
class Match:
    codes: list[str]
    matched_by: str
    problem: str = ""  # why there is no code
    candidates: list[str] = field(default_factory=list)


def match_brand_strength(cat: Catalogue, brand: str, strength_: str | None) -> Match:
    """Packages with this brand key and strength, if they are one product; else no match."""
    if not strength_:
        return Match([], "", "no strength")
    entries = cat.by_brand.get(brand, [])
    if not entries:
        return Match([], "", "brand not in our lists")
    codes = sorted(c for c, s in entries if s == strength_)
    if not codes:
        return Match([], "", f"no {brand} package with strength {strength_}")
    products = {c[:7] for c in codes}
    if len(products) > 1:
        return Match([], "", "ambiguous: several products", codes)
    return Match(codes, "brand+strength")


def match_package_name(cat: Catalogue, name: str) -> Match:
    """A list row without a usable barcode: exact name, else brand + strength."""
    exact = cat.by_name.get(name_key(name), [])
    if len(exact) == 1:
        return Match(exact, "name")
    return match_brand_strength(cat, name_key(split_name(name)[0]), strength_key(name))


_LATIN = re.compile(r"[A-Za-z]")
_WORD = re.compile(r"[^\s,;:()&«»\"]+")


def products_in_text(cat: Catalogue, text: str) -> list[tuple[str, str, list[str]]]:
    """Brands of our lists named in a sentence, each with the strengths after it.

    'EMEND 125 mg σκληρά καψάκια & EMEND 80 mg' -> [('EMEND', 'EMEND', ['125MG']),
    ('EMEND', 'EMEND', ['80MG'])]. A brand must be written with at least one Latin
    letter (so Greek words such as 'ΤΗΝ' never read as brands); up to 4 words.
    """
    words = list(_WORD.finditer(text))
    found = []  # (start, end, brand key, as written)
    i = 0
    while i < len(words):
        for n in (4, 3, 2, 1):
            group = words[i : i + n]
            if len(group) < n:
                continue
            written = text[group[0].start() : group[-1].end()]
            key = name_key(written)
            if len(key) >= 3 and _LATIN.search(written) and cat.is_brand(key):
                found.append((group[0].start(), group[-1].end(), key, written))
                i += n
                break
        else:
            i += 1
    out = []
    for j, (_, end, key, written) in enumerate(found):
        stop = found[j + 1][0] if j + 1 < len(found) else len(text)
        out.append((key, written, text_strengths(text[end:stop])))
    return out


# --- EMA ----------------------------------------------------------------------------

_EMA_COLUMNS = {
    "Category": "category",
    "Medicine affected": "name",
    "Supply shortage status": "status",
    "International non-proprietary name (INN) or common name": "inn",
    "Pharmaceutical forms affected": "forms",
    "Strengths affected": "strengths",
    "Availability of alternatives": "alternatives",
    "Start of shortage date": "start",
    "Expected resolution date": "end",
    "Expected resolution": "end_text",
    "First published date": "first_published",
    "Last updated date": "last_updated",
    "Shortage URL": "url",
}


def read_ema_shortages(path: Path) -> tuple[str, list[dict[str, str]]]:
    """(generated at, rows) of the EMA shortages table; a missing column is an error."""
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True)
    try:
        rows = [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]
    finally:
        wb.close()
    if not rows or rows[0][:2] != ["Content type:", "Shortage"]:
        raise ShortageError(f"{path.name}: not the EMA shortages table")
    generated = _clean(rows[0][3]) if len(rows[0]) > 3 else ""
    for i, r in enumerate(rows):
        if r and r[0] == "Category":
            header = [_clean(c) for c in r]
            break
    else:
        raise ShortageError(f"{path.name}: no header row")
    missing = [c for c in _EMA_COLUMNS if c not in header]
    if missing:
        raise UnknownHeader(f"{path.name}: missing columns {missing}")
    idx = {f: header.index(c) for c, f in _EMA_COLUMNS.items()}
    out = []
    for r in rows[i + 1 :]:
        if not any(r):
            continue
        rec = {f: _clean(r[j]) if j < len(r) else "" for f, j in idx.items()}
        if rec["status"] not in EMA_STATUSES:
            raise ShortageError(f"EMA: unknown status {rec['status']!r} ({rec['name']})")
        out.append(rec)
    return generated, out


def read_ema_links(path: Path | None) -> dict[str, list[str]]:
    """name_key(EMA medicine name) -> our package codes, from ingest.ema's ema_links.csv."""
    links: dict[str, list[str]] = {}
    if path is None or not path.exists():
        return links
    with path.open(encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            links.setdefault(name_key(r["ema_name"]), []).append(r["eof_code"])
    return links


def match_ema(cat: Catalogue, links: Mapping[str, list[str]], rec: Mapping[str, str]) -> Match:
    """Through ema_links.csv (centrally authorised); else brand + strength, as for notices."""
    wanted = text_strengths(rec["strengths"])
    codes = sorted(set(links.get(name_key(rec["name"]), [])))
    if codes:
        if not wanted:
            return Match(codes, "ema_link")
        kept = [c for c in codes if strength_key(cat.names.get(c, "")) in wanted]
        if not kept:
            return Match([], "", f"no linked package with strength {wanted}", codes)
        return Match(kept, "ema_link+strength")
    brands = products_in_text(cat, rec["name"])
    if not brands:
        return Match([], "", "not linked to our packages, no brand of our lists in the name")
    found: list[str] = []
    problems, candidates = [], []
    for brand, _, _ in brands:
        for s in wanted or [None]:
            m = match_brand_strength(cat, brand, s)
            found += m.codes
            if not m.codes:
                problems.append(f"{brand} {s or ''}: {m.problem}".strip())
                candidates += m.candidates
    if found:
        return Match(sorted(set(found)), "brand+strength")
    return Match([], "", "; ".join(problems), candidates)


# --- build ----------------------------------------------------------------------------


def _row(**kw: str) -> dict[str, str]:
    return {f: kw.get(f, "") for f in FIELDS}


def _file_info(cache: Cache, url: str) -> dict:
    e = cache.latest(url) or {}
    return {k: e.get(k, "") for k in ("sha256", "bytes", "retrieved_at")} | {"url": url}


def _sha_file(path: Path) -> str:
    return _sha256(path.read_bytes())


def build(
    cache: Cache,
    packages: Path,
    otc: Path | None,
    ema_links: Path | None,
    today: date,
) -> tuple[list[dict[str, str]], dict]:
    cat = Catalogue.read(packages, otc)
    posts = known_posts(cache)
    out: list[dict[str, str]] = []
    report: dict = {
        "generated": today.isoformat(),
        "inputs": {
            name: {"path": str(p), "sha256": _sha_file(p)}
            for name, p in (("packages", packages), ("otc", otc), ("ema_links", ema_links))
            if p is not None and p.exists()
        },
        "current": {},
        "pending": [],
        "unmatched": [],
        "rejected_matches": [],
        "not_in_catalogue": [],
        "sources": [],
        "history": [
            {
                "post_id": p.post_id,
                "kind": p.kind,
                "published": p.published.isoformat(),
                "list_date": p.list_date.isoformat(),
                "title": p.title,
                "url": p.url,
                "downloaded": cache.latest(p.url) is not None,
            }
            for p in sorted(posts.values(), key=Post.key, reverse=True)
        ],
    }

    def note(rows: list[dict[str, str]], m: Match | None, text: str, list_type: str, source: str) -> None:
        if m is not None and m.candidates and not m.codes:
            report["rejected_matches"].append(
                {"list_type": list_type, "text": text, "reason": m.problem, "candidates": m.candidates}
            )
        for r in rows:
            if not r["eof_code"]:
                reason = m.problem if m else "no barcode"
                report["unmatched"].append(
                    {"list_type": list_type, "name": text, "reason": reason, "source_url": source}
                )
            elif not r["in_catalogue"]:
                report["not_in_catalogue"].append(
                    {"list_type": list_type, "eof_code": r["eof_code"], "name": r["name"]}
                )

    # EOF lists: the newest post of each kind that has its file on disk
    for kind in LIST_KINDS:
        of_kind = sorted((p for p in posts.values() if p.kind == kind), key=Post.key, reverse=True)
        info: dict = {"newest_post": of_kind[0].url if of_kind else None}
        report["current"][kind] = info
        chosen = files = None
        for p in of_kind:
            page = cache.file(p.url)
            if page is None:
                continue
            _, fs = parse_post(page.read_text(encoding="utf-8"), p.url)
            if fs and all(cache.file(f) for f in fs):
                chosen, files = p, fs
                break
        if of_kind and chosen is not of_kind[0]:
            report["pending"].append(
                {
                    "kind": kind,
                    "post": of_kind[0].url,
                    "title": of_kind[0].title,
                    "reason": "newest list not downloaded" + (", using an older one" if chosen else ""),
                }
            )
        if chosen is None:
            info["rows"] = 0
            continue
        info |= {
            "post": chosen.url,
            "title": chosen.title,
            "published": chosen.published.isoformat(),
            "list_date": chosen.list_date.isoformat(),
            "stale": chosen is not of_kind[0],
            "files": [_file_info(cache, f) for f in files],
        }
        report["sources"] += [_file_info(cache, f) | {"role": kind} for f in files]
        rows: list[dict[str, str]] = []
        sections: Counter = Counter()
        for f in files:
            path = cache.file(f)
            assert path is not None
            if not f.lower().endswith(".pdf"):
                raise ShortageError(f"{f}: only PDF lists are read so far")
            try:
                parsed = parse_tables(pdf_tables(path))
            except ScannedDocument as e:
                report["pending"].append(
                    {"kind": kind, "post": chosen.url, "title": chosen.title, "file": f, "reason": str(e)}
                )
                continue
            for lr in parsed:
                sections[lr.section] += 1
                rows += _list_row(cat, kind, chosen, f, lr, note)
        info["rows"] = len(rows)
        info["sections"] = dict(sections)
        # the same package twice in one list, e.g. two separate periods: kept, listed here
        twice = Counter(r["eof_code"] for r in rows if r["eof_code"])
        info["repeated_codes"] = sorted(c for c, n in twice.items() if n > 1)
        out += rows

    # EOF notices of the last NOTICE_DAYS days
    for kind, days in NOTICE_DAYS.items():
        notices = [p for p in current_posts(posts.values(), today) if p.kind == kind]
        info = {"days": days, "posts": [], "rows": 0}
        report["current"][kind] = info
        for p in notices:
            page = cache.file(p.url)
            if page is None:
                report["pending"].append(
                    {"kind": kind, "post": p.url, "title": p.title, "reason": "not downloaded"}
                )
                continue
            info["posts"].append(p.url)
            report["sources"].append(_file_info(cache, p.url) | {"role": kind})
            body, _ = parse_post(page.read_text(encoding="utf-8"), p.url)
            rows = _notice_rows(cat, p, body, note)
            info["rows"] += len(rows)
            out += rows

    # EMA
    ema_file = cache.file(EMA_SHORTAGES_XLSX)
    info = {"rows": 0}
    report["current"]["eu_shortage"] = info
    if ema_file is not None:
        generated, recs = read_ema_shortages(ema_file)
        links = read_ema_links(ema_links)
        report["sources"].append(_file_info(cache, EMA_SHORTAGES_XLSX) | {"role": "eu_shortage"})
        info |= {"generated": generated, "statuses": dict(Counter(r["status"] for r in recs))}
        if not links:
            info["warning"] = "no ema_links.csv: EMA entries cannot be matched"
        for rec in recs:
            if rec["status"] not in EMA_CURRENT:
                continue
            m = match_ema(cat, links, rec)
            base = dict(
                list_type="eu_shortage",
                status=rec["status"],
                published=_ema_date(rec["last_updated"] or rec["first_published"]),
                valid_until=_ema_date(rec["end"]),
                start=_ema_date(rec["start"]),
                section=rec["end_text"] and f"Expected resolution: {rec['end_text']}",
                alternatives=rec["alternatives"],
                source_url=rec["url"],
                source_file=EMA_SHORTAGES_XLSX,
                source_title=f"EMA, medicine shortages: {rec['name']}",
            )
            rows = [
                _row(
                    **base, eof_code=c, name=cat.names[c], matched_by=m.matched_by, in_catalogue=cat.source[c]
                )
                for c in m.codes
                if c in cat.names
            ] or [_row(**base, name=rec["name"])]
            note(rows, m, rec["name"], "eu_shortage", rec["url"])
            info["rows"] += len(rows)
            out += rows
    else:
        report["pending"].append(
            {"kind": "eu_shortage", "file": EMA_SHORTAGES_XLSX, "reason": "not downloaded"}
        )

    out.sort(key=lambda r: (r["list_type"], r["eof_code"] == "", r["eof_code"], r["name"]))
    by_type: dict[str, Counter] = {}
    for r in out:
        c = by_type.setdefault(r["list_type"], Counter())
        c["rows"] += 1
        c["with_eof_code"] += bool(r["eof_code"])
        c[f"in_{r['in_catalogue']}" if r["in_catalogue"] else "not_in_catalogue"] += bool(r["eof_code"])
        c[f"matched_by:{r['matched_by'] or '-'}"] += 1
    report["counts"] = {k: dict(v) for k, v in sorted(by_type.items())}
    return out, report


def _ema_date(text: str) -> str:
    m = _DMY.fullmatch(text.strip()) if text else None
    return _dmy(m).isoformat() if m else ""


def _list_row(
    cat: Catalogue, kind: str, post: Post, file_url: str, lr: ListRow, note
) -> list[dict[str, str]]:
    f = lr.fields
    list_type = LIST_TYPE[kind]
    code = eof_code_from_barcode(f["barcode"])
    m = None
    if code:
        codes, matched_by = [code], "barcode"
    else:
        m = match_package_name(cat, f["name"])
        codes, matched_by = m.codes, m.matched_by
    rows = [
        _row(
            eof_code=c,
            list_type=list_type,
            status=f.get("reason", ""),
            published=post.list_date.isoformat(),
            valid_until=iso_date(f.get("end", "")),
            start=iso_date(f.get("start") or f.get("discontinued", "")),
            name=f["name"],
            barcode=f["barcode"],
            section=lr.section,
            alternatives=f.get("alternatives", ""),
            matched_by=matched_by,
            in_catalogue=cat.source.get(c, ""),
            source_url=post.url,
            source_file=file_url,
            source_title=post.title,
        )
        for c in codes or [""]
    ]
    note(rows, m, f["name"], list_type, post.url)
    return rows


def _notice_rows(cat: Catalogue, post: Post, body: str, note) -> list[dict[str, str]]:
    """Rows of a notice: the brands its title names, each with the strengths that follow it in
    the title or, when the title gives none, in the text of the post. Other products the text
    may mention are never matched."""
    list_type = LIST_TYPE[post.kind]
    base = dict(
        list_type=list_type,
        status=post.title,
        published=post.published.isoformat(),
        source_url=post.url,
        source_title=post.title,
    )
    rows = []
    found = products_in_text(cat, post.title)
    if not found:
        r = [_row(**base, name=post.title)]
        note(r, Match([], "", "no brand of our lists in the title"), post.title, list_type, post.url)
        return r
    in_body: dict[str, list[str]] = {}
    for brand, _, strengths in products_in_text(cat, body):
        in_body.setdefault(brand, []).extend(x for x in strengths if x not in in_body.get(brand, []))
    for brand, written, strengths in found:
        for s in strengths or in_body.get(brand) or [None]:
            m = match_brand_strength(cat, brand, s)
            label = f"{written} {s or ''}".strip()
            r = [
                _row(
                    **base, eof_code=c, name=cat.names[c], matched_by=m.matched_by, in_catalogue=cat.source[c]
                )
                for c in m.codes
            ] or [_row(**base, name=label)]
            note(r, m, label, list_type, post.url)
            rows += r
    return rows


def write_csv(rows: Iterable[Mapping[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


# --- command line ---------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m ingest.shortages", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="discover and download (polite, incremental)")
    f.add_argument("--out", type=Path, default=Path("data/shortages"))
    f.add_argument("--max-requests", type=int, default=30)
    f.add_argument("--backfill", action="store_true", help="read every listing page (once)")
    f.add_argument("--no-ema", action="store_true", help="skip the EMA shortages table")
    b = sub.add_parser("build", help="write shortages.csv and shortages_report.json (offline)")
    b.add_argument("--cache", type=Path, default=Path("data/shortages"))
    b.add_argument("--packages", type=Path, default=Path("data/current/packages.csv"))
    b.add_argument("--otc", type=Path, default=Path("data/current/otc_packages.csv"))
    b.add_argument("--ema-links", type=Path, help="default: ema_links.csv next to --packages")
    b.add_argument("--out", type=Path, default=Path("data/current"))
    b.add_argument("--today", type=date.fromisoformat, default=date.today())
    a = ap.parse_args(argv)

    if a.cmd == "fetch":
        if (a.out / "STOP").exists():
            print(f"{a.out / 'STOP'} exists: not fetching", file=sys.stderr)
            return 2
        client = Client(Cache(a.out), a.max_requests)
        try:
            summary = fetch(client, date.today(), backfill=a.backfill, ema=not a.no_ema)
        except BudgetExhausted as e:
            print(f"stopped: {e}; run again to continue", file=sys.stderr)
            return 1
        except Blocked as e:
            print(f"stopped, not retrying: {e}", file=sys.stderr)
            return 3
        except ShortageError as e:
            print(f"unexpected page or file: {e}", file=sys.stderr)
            return 4
        print(json.dumps(summary, ensure_ascii=False))
        return 0

    ema_links = a.ema_links or a.packages.parent / "ema_links.csv"
    try:
        rows, report = build(
            Cache(a.cache), a.packages, a.otc if a.otc.exists() else None, ema_links, a.today
        )
    except ShortageError as e:
        print(f"unexpected page or file: {e}", file=sys.stderr)
        return 4
    write_csv(rows, a.out / "shortages.csv")
    (a.out / "shortages_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(
        json.dumps(
            {"rows": len(rows), "counts": report["counts"], "pending": len(report["pending"])},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
