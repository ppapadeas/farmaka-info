"""Discovery and download of Ministry of Health price bulletins (moh.gov.gr).

The ministry site rejects requests without a browser-like User-Agent, so every
request sends one. Be polite: one request at a time, a pause between requests,
and keep a local cache of everything downloaded (files never change once
published; corrections are published as new articles).
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import time
import unicodedata
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urljoin

import requests

BASE = "https://www.moh.gov.gr"
PRICE_BULLETINS = f"{BASE}/articles/times-farmakwn/deltia-timwn"
REIMBURSEMENT = f"{BASE}/articles/times-farmakwn/epitroph-aksiologhshs-kai-apozhmiwshs-farmakwn"
# Non-prescription medicines (ΜΗ.ΣΥ.ΦΑ.): indicative retail prices, own listing.
OTC_BULLETINS = f"{BASE}/articles/times-farmakwn/deltia-timwn-mhsyfa"

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
PAUSE_SECONDS = 3.0  # the site started resetting connections after ~400 requests at 1.5 s
RETRIES = 4  # the site occasionally stalls; back off 5, 10, 20, 40 s

_ITEM_RE = re.compile(
    r'<span class="datetime">(?P<date>\d{2}/\d{2}/\d{4})</span>.*?'
    r'<a href="(?P<href>[^"]+)">(?P<title>.*?)</a>',
    re.DOTALL,
)
_ATTACH_RE = re.compile(
    r'<a[^>]+href="(?P<href>[^"]*fdl=(?P<fdl>\d+)[^"]*)"[^>]*>(?P<label>.*?)</a>', re.DOTALL
)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", text))).strip()


def _norm(text: str) -> str:
    """Lowercase, strip accents, collapse whitespace: titles come in mixed case."""
    t = unicodedata.normalize("NFD", text.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", t).strip()


def classify(title: str) -> str:
    """Map a bulletin title to a kind used by the merge step.

    full      : complete consolidated price list (baseline)
    revision  : annual revised price bulletin ("Δελτίο αναθεωρημένων τιμών", December);
                market-wide repricing, candidate baseline (to be verified per file)
    delta     : adds or changes package prices
    amendment : amends, corrects ("Ορθή Επανάληψη") or annuls an earlier decision;
                needs manual review
    cannabis  : medical cannabis bulletins (separate product family, out of scope for now)
    other     : anything else
    """
    t = _norm(title)
    if "κανναβ" in t:
        return "cannabis"
    # checked before amendments: the consolidated list itself is often an "Ο.Ε." (correction)
    if "επικαιροποιηση δελτιου τιμων φαρμακων" in t and "διοικητικων μεταβολων" in t:
        return "full"
    if any(k in t for k in ("τροποποιηση", "ορθη επαναληψη", "ακυρωση")):
        return "amendment"
    if "δελτιο αναθεωρημενων τιμων" in t:
        return "revision"
    if "δελτιο τιμων" in t or "δελτιο τιμολογησης" in t:
        return "delta"
    return "other"


_OTC_AMENDS_DECISION = re.compile(r"\b(ορθη επαναληψη|τροποποιηση|ακυρωση) (της|του|των)\b")


def classify_otc(title: str) -> str:
    """Map a ΜΗ.ΣΥ.ΦΑ. (non-prescription) bulletin title to a kind; see ingest/otc.py.

    Kept apart from `classify`: the titles of this section follow other patterns.

    repricing : revision of the whole list ("Αναθεώρηση καταλόγου ΜΗ.ΣΥ.ΦΑ. για το
                έτος 2025", yearly; older: "Αναθεώρηση Δελτίου Τιμών…", "Πρώτη
                Αναθεώρηση έτους 2018…") and the first list of 2017 ("Κατάλογος
                ενδεικτικών τιμών… ΟΡΘΗ ΕΠΑΝΑΛΗΨΗ", a republished list)
    update    : list updated with administrative changes ("Επικαιροποίηση Δελτίου
                Τιμών ΜΗ.ΣΥ.ΦΑ. λόγω διοικητικών μεταβολών"), also as a
                correction ("ΟΡΘΗ ΕΠΑΝΑΛΗΨΗ: Επικαιροποίηση…") or together with
                a new-products bulletin ("Δελτίο Τιμών Νέων… και Επικαιροποίηση…")
    new       : bulletin of new products, mostly quarterly ("Δελτίο Τιμών Νέων
                ΜΗ.ΣΥ.ΦΑ. 1ου Τριμήνου 2026"; until 2019 "Κατάλογος ενδεικτικών
                τιμών λιανικής πώλησης νέων ΜΗ.ΣΥ.ΦΑ.…")
    amendment : corrects, amends or annuls an earlier decision ("Ορθή Επανάληψη
                της Δ3(α) 6320/11-3-2024 Απόφασης…"); needs manual review
    other     : anything else
    """
    t = _norm(title)
    if _OTC_AMENDS_DECISION.search(t):
        return "amendment"
    if "επικαιροποιηση δελτι" in t:
        return "update"
    if "αναθεωρηση" in t or "ανατιμολογηση" in t:
        return "repricing"
    if "τιμων νεων" in t or "νεων μη συνταγογραφουμενων" in t:
        return "new"
    if "καταλογος ενδεικτικων τιμων" in t:
        return "repricing"
    if any(k in t for k in ("τροποποιηση", "ορθη επαναληψη", "ακυρωση")):
        return "amendment"
    return "other"


@dataclass
class Attachment:
    fdl: str
    label: str
    url: str

    @property
    def filename(self) -> str:
        return self.label.split(" Μέγεθος:")[0].strip()

    @property
    def ext(self) -> str:
        return Path(self.filename).suffix.lower().lstrip(".")


@dataclass
class Article:
    article_id: int
    date: str  # DD/MM/YYYY as published
    title: str
    url: str
    kind: str
    attachments: list[Attachment] = field(default_factory=list)
    checked: bool = False  # article page was read (it may have no attachments)

    @property
    def iso_date(self) -> str:
        d, m, y = self.date.split("/")
        return f"{y}-{m}-{d}"


class Client:
    def __init__(self, cache_dir: Path | None = None):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = USER_AGENT
        self.cache_dir = cache_dir
        self._last = 0.0

    def get(self, url: str) -> requests.Response:
        for attempt in range(RETRIES + 1):
            wait = PAUSE_SECONDS - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            try:
                r = self.s.get(url, timeout=60)
                self._last = time.monotonic()
                if r.status_code >= 500:
                    raise requests.HTTPError(f"{r.status_code} for {url}", response=r)
                r.raise_for_status()
                return r
            except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as e:
                self._last = time.monotonic()
                status = getattr(getattr(e, "response", None), "status_code", None)
                if attempt == RETRIES or (status is not None and status < 500):
                    raise
                time.sleep(5 * 2**attempt)
        raise AssertionError("unreachable")

    def list_articles(
        self,
        section: str = PRICE_BULLETINS,
        max_pages: int = 50,
        first_page: int = 1,
        kind_of: Callable[[str], str] = classify,
    ) -> list[Article]:
        out: list[Article] = []
        seen: set[str] = set()
        for page in range(first_page, first_page + max_pages):
            r = self.get(f"{section}?page={page}")
            items = list(_ITEM_RE.finditer(r.text))
            new = 0
            for m in items:
                href = m["href"]
                if href in seen or not href.startswith(section + "/"):
                    continue
                seen.add(href)
                new += 1
                slug = href.rsplit("/", 1)[-1]
                aid = int(slug.split("-", 1)[0]) if slug[:1].isdigit() else -1
                title = _clean(m["title"])
                out.append(Article(aid, m["date"], title, href, kind_of(title)))
            if new == 0:
                break
        return out

    def load_attachments(self, art: Article) -> Article:
        r = self.get(art.url)
        for m in _ATTACH_RE.finditer(r.text):
            url = urljoin(art.url, html.unescape(m["href"]))
            if not url.startswith(art.url):
                url = f"{art.url}?fdl={m['fdl']}"
            art.attachments.append(Attachment(m["fdl"], _clean(m["label"]), url))
        return art

    def download(self, att: Attachment, dest_dir: Path, art: Article) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / f"moh_{art.article_id}_{att.fdl}.{att.ext or 'bin'}"
        if path.exists():
            return path
        r = self.get(att.url)
        path.write_bytes(r.content)
        return path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_manifest(path: Path) -> list[Article]:
    if not path.exists():
        return []
    out = []
    for a in json.loads(path.read_text(encoding="utf-8")):
        atts = [Attachment(**x) for x in a.pop("attachments", [])]
        out.append(Article(**a, attachments=atts))
    return out


def write_manifest(articles: list[Article], path: Path) -> None:
    path.write_text(
        json.dumps([asdict(a) for a in articles], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
