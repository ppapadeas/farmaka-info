"""EMA product information in Greek (ΠΧΠ + ΦΟΧ) as structured JSON.

download  one Greek product-information PDF per EMA product of data/current/ema_links.csv,
          one request at a time with the ingest.ema client (>= 5 s apart, backs off on
          429/5xx). Files are kept as downloaded, never changed, with sha256 and
          retrieved_at in <cache>/manifest.json; a product already on disk is skipped, so
          an interrupted run continues where it stopped. --limit N for batches.
parse     every downloaded PDF -> <out>/<EMEA-H-C-000071>.json with the SmPCs (Annex I)
          and the package leaflets (Annex III B) as numbered sections; Annex II and the
          labelling (Annex III A) are skipped, their page ranges recorded. A file that
          does not parse cleanly is not written: it is listed with the reasons in
          <out>_report.json and the run goes on to the next file.
lines     print the text lines of a PDF as the parser sees them (the fixture format of
          tests/test_ema_pi.py), for debugging.

How a PDF is read (pdfplumber): text lines with their font (bold/italic) and position;
ruled tables as rows of cells. Running headers/footers (the same text in the top or
bottom margin of many pages) and page numbers are removed. The divider pages
("ΠΑΡΑΡΤΗΜΑ Ι", "Α. ΕΠΙΣΗΜΑΝΣΗ", "Β. ΦΥΛΛΟ ΟΔΗΓΙΩΝ ΧΡΗΣΗΣ", ...) split the file into parts.
Section headings are found by number and title (the QRD template titles, compared
loosely) or by number, bold type and order. Lines between headings become paragraphs,
bullet lists, sub-headings and tables. Text that cannot be placed stays in the nearest
preceding section; every character of the SmPC and PIL pages is counted in and out.
A file fails (reported, not written) when pdfplumber reads fewer characters than pdfium
(fonts pdfminer cannot decode), when it is not in Greek, when a divider page is missing,
or when an SmPC lacks one of 4.1-4.6, 4.8 or a leaflet one of its sections 1-6.

Usage:
    python -m ingest.ema_pi download --links data/current/ema_links.csv --cache data/ema/pi
    python -m ingest.ema_pi parse --cache data/ema/pi --out data/current/ema_pi
    python -m ingest.ema_pi lines data/ema/pi/EMEA-H-C-000071_....pdf --pages 1-5
"""

from __future__ import annotations

import argparse
import csv
import difflib
import json
import os
import re
import statistics
import sys
import unicodedata
from collections import Counter
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import groupby
from pathlib import Path

import requests

from ingest.ema import Client, _fold, _sha256

SCHEMA_VERSION = 1
WORD_GAP = 0.15  # a gap wider than 0.15 x font size between two characters is a space
LINE_GAP = 4.5  # characters whose tops differ by up to 4.5 pt share a line (sub/superscripts)
MAX_FAILURES_IN_A_ROW = 5  # stop downloading: the EMA is refusing us, do not insist
PUBLISHER = "European Medicines Agency"
ATTRIBUTION = "Πηγή: Ευρωπαϊκός Οργανισμός Φαρμάκων (EMA), πληροφορίες προϊόντος (EPAR), ελληνική έκδοση."


# --- download ----------------------------------------------------------------------


@dataclass(frozen=True)
class PiSource:
    product_number: str  # EMEA/H/C/000071
    name: str
    url: str

    @property
    def file_stem(self) -> str:
        return safe_name(self.product_number)


def safe_name(product_number: str) -> str:
    """'EMEA/H/C/000071' -> 'EMEA-H-C-000071' (file name on every system)."""
    return product_number.replace("/", "-")


def read_sources(links: Path) -> list[PiSource]:
    """Distinct EMA products with a Greek PI URL, sorted by product number."""
    with links.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    need = {"ema_product_number", "ema_name", "pi_el_url"}
    if rows and not need <= rows[0].keys():
        raise ValueError(f"{links}: missing columns {sorted(need - rows[0].keys())}")
    by_number: dict[str, PiSource] = {}
    for r in rows:
        if not r["pi_el_url"]:
            continue
        s = PiSource(r["ema_product_number"], r["ema_name"], r["pi_el_url"])
        old = by_number.setdefault(s.product_number, s)
        if old.url != s.url:
            raise ValueError(f"{links}: two PI URLs for {s.product_number}: {old.url} {s.url}")
    return [by_number[k] for k in sorted(by_number)]


def read_manifest(cache: Path) -> list[dict]:
    path = cache / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def _write_json_atomic(path: Path, data: object, indent: int | None = 1) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=indent), encoding="utf-8")
    os.replace(tmp, path)


def current_entries(cache: Path, manifest: list[dict] | None = None) -> dict[str, dict]:
    """{product number: latest manifest entry whose file is on disk}."""
    out: dict[str, dict] = {}
    for e in read_manifest(cache) if manifest is None else manifest:
        if (cache / e["file"]).exists():
            out[e["product_number"]] = e
    return out


class NotAPdf(ValueError):
    pass


def download_one(client: Client, src: PiSource, cache: Path, manifest: list[dict]) -> dict:
    """Download src.url into cache; append to manifest (and save it) when the bytes are new."""
    cache.mkdir(parents=True, exist_ok=True)
    part = cache / f"{src.file_stem}.pdf.part"
    try:
        with client.request("GET", src.url, stream=True) as r:
            r.raise_for_status()
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            with part.open("wb") as fh:
                for chunk in r.iter_content(1 << 16):
                    fh.write(chunk)
            last_modified = r.headers.get("Last-Modified", "")
            ctype = r.headers.get("Content-Type", "")
    except requests.RequestException:
        part.unlink(missing_ok=True)  # e.g. the connection dropped in the middle of the file
        raise
    with part.open("rb") as fh:
        head = fh.read(5)
    if head != b"%PDF-":
        part.unlink()
        raise NotAPdf(f"not a PDF ({ctype or 'no Content-Type'}, starts {head!r})")
    digest = _sha256(part)
    prev = current_entries(cache, manifest).get(src.product_number)
    if prev and prev["sha256"] == digest:
        part.unlink()
        return prev
    path = cache / f"{src.file_stem}_{stamp}.pdf"
    n = 1
    while path.exists():
        n += 1
        path = cache / f"{src.file_stem}_{stamp}-{n}.pdf"
    os.replace(part, path)
    entry = {
        "product_number": src.product_number,
        "name": src.name,
        "url": src.url,
        "file": path.name,
        "sha256": digest,
        "bytes": path.stat().st_size,
        "retrieved_at": stamp,
        "last_modified": last_modified,
    }
    manifest.append(entry)
    _write_json_atomic(cache / "manifest.json", manifest)
    return entry


def download(
    client: Client,
    sources: list[PiSource],
    cache: Path,
    limit: int | None = None,
    refresh: bool = False,
    log=print,
) -> tuple[int, list[tuple[str, str]]]:
    """(downloaded, failures as (product number, reason)). Skips products on disk unless
    refresh; stops after MAX_FAILURES_IN_A_ROW consecutive failures."""
    manifest = read_manifest(cache)
    have = current_entries(cache, manifest)
    todo = [s for s in sources if refresh or s.product_number not in have]
    if limit is not None:
        todo = todo[:limit]
    log(f"{len(sources)} products, {len(have)} on disk, {len(todo)} to download")
    done, failures, in_a_row = 0, [], 0
    for i, src in enumerate(todo, 1):
        try:
            e = download_one(client, src, cache, manifest)
        except (requests.RequestException, NotAPdf) as exc:
            reason = f"{type(exc).__name__}: {exc}"
            failures.append((src.product_number, reason))
            log(f"[{i}/{len(todo)}] FAILED {src.product_number} {src.name}: {reason}")
            in_a_row += 1
            if in_a_row >= MAX_FAILURES_IN_A_ROW:
                log(f"stopping: {in_a_row} failures in a row")
                break
            continue
        in_a_row = 0
        done += 1
        log(f"[{i}/{len(todo)}] {src.product_number} {src.name}: {e['bytes']} bytes")
    return done, failures


# --- text lines --------------------------------------------------------------------


@dataclass(frozen=True)
class Line:
    """One text line of a page (or one row of a ruled table, when `table` > 0)."""

    page: int
    x0: float
    x1: float
    top: float
    bottom: float
    size: float
    text: str
    bold: bool = False  # most characters in a bold font
    italic: bool = False
    table: int = 0  # > 0: row of table number `table`, cells in `cells`
    cells: tuple[str, ...] = ()
    w1: float = 0.0  # width of the first word; 0: estimated from the text

    def first_word_width(self) -> float:
        if self.w1:
            return self.w1
        return 0.5 * self.size * len(self.text.split(" ", 1)[0])


@dataclass
class Page:
    number: int
    width: float
    height: float
    lines: list[Line]
    chars_read: int = 0  # visible characters pdfplumber read on the page, doubles included
    chars_pdfium: int | None = None  # the same as pdfium reads them (None: not checked)


_BOLD_FONT = re.compile(r"bold|black|heavy|semibold|demi|[,-]B[dD]?$", re.IGNORECASE)
_ITALIC_FONT = re.compile(r"italic|oblique|[,-]It?$", re.IGNORECASE)


def clean(text: str) -> str:
    """NFC, single spaces, no zero-width spaces; U+2206 INCREMENT, which some files use
    for the Greek capital delta ("Ο\u2206ΗΓΙΩΝ"), as \u0394."""
    t = unicodedata.normalize("NFC", text).replace("\u2206", "\u0394").replace("\u200b", "")
    return " ".join(t.split())


def greek_share(texts: Iterable[str]) -> float:
    """Share of Greek letters among all letters (0 when there are none)."""
    greek = other = 0
    for t in texts:
        for c in t:
            if c.isalpha():
                if "\u0370" <= c <= "\u03ff" or "\u1f00" <= c <= "\u1fff":
                    greek += 1
                else:
                    other += 1
    return greek / (greek + other) if greek + other else 0.0


def fold(text: str) -> str:
    """Upper case, no accents, Greek capitals that look Latin as Latin, single spaces."""
    return " ".join(_fold(text).split())


def nonspace(text: str) -> int:
    """Visible characters (no spaces, no soft hyphens): what must not get lost."""
    return sum(1 for c in text if not c.isspace() and c != "\u00ad")


def join_lines(a: str, b: str) -> str:
    """Join two lines of one paragraph. A soft hyphen at the end of a line is a break
    point and goes; a real hyphen stays, without a space (the EMA texts are not
    hyphenated by the typesetter: "ινσουλινο-|εξαρτώμενους", "β-|αναστολείς")."""
    if not a:
        return b
    if not b:
        return a
    if a.endswith("\u00ad"):
        return a[:-1] + b
    if re.search(r"[^\W\d_]-$", a) or a.endswith("/"):
        return a + b
    return f"{a} {b}"


def read_pdf(path: Path, pages: Iterable[int] | None = None) -> list[Page]:
    """Text lines and ruled tables of each page (1-based numbers; all pages by default).
    Each page also carries how many visible characters pdfplumber read and how many
    pdfium reads, so that characters pdfminer drops (fonts with an embedded CMap it
    cannot decode) are noticed instead of silently missing."""
    out: list[Page] = []
    expected = _pdfium_counts(path)
    with _open_pdf(path) as pdf:
        wanted = set(pages) if pages is not None else None
        for p in pdf.pages:
            if wanted is not None and p.page_number not in wanted:
                continue
            # the check against pdfium counts what pdfminer decoded, doubles included:
            # pdfium drops some doubles (fake bold) and not others (the repeated labels
            # of a figure in the Alimta SmPC), and only a shortfall matters
            chars_read = sum(_glyphs(c["text"]) for c in p.chars)
            # "fake bold": some files draw bold text twice, a fraction of a point apart;
            # read as is, "ALGOFREN" becomes "AALLGGOOFFRREENN"
            p = _without_doubles(p)
            text_page, table_lines = _tables(p)
            raw = text_page.extract_text_lines(
                return_chars=True, x_tolerance_ratio=WORD_GAP, y_tolerance=LINE_GAP
            )
            lines = [ln for r in raw if (ln := _line(p, r))]
            lines.extend(table_lines)
            lines.sort(key=lambda ln: (round(ln.top, 1), ln.x0))
            pg = Page(p.page_number, float(p.width), float(p.height), lines)
            pg.chars_read = chars_read
            pg.chars_pdfium = expected[p.page_number - 1] if p.page_number <= len(expected) else None
            out.append(pg)
    return out


def _without_doubles(page, tolerance: float = 1):
    """The page without characters drawn twice in one place (same text, font and size, up
    to `tolerance` pt apart). The same as pdfplumber's dedupe_chars, which puts its result
    back in order with list.index for every character: 3-4 times the time to read a page."""
    from pdfplumber.utils import cluster_objects

    chars = page.chars

    def same(i: int) -> tuple:
        c = chars[i]
        return (c["upright"], c["text"], c["fontname"], c["size"])

    keep: set[int] = set()
    for _, group in groupby(sorted(range(len(chars)), key=same), key=same):
        for row in cluster_objects(list(group), lambda i: chars[i]["doctop"], tolerance):
            for spot in cluster_objects(row, lambda i: chars[i]["x0"], tolerance):
                keep.add(min(spot, key=lambda i: (chars[i]["doctop"], chars[i]["x0"], i)))
    if len(keep) == len(chars):
        return page
    kept = {id(chars[i]) for i in keep}
    return page.filter(lambda o: o.get("object_type") != "char" or id(o) in kept)


def _glyphs(text: str) -> int:
    """Characters for the pdfplumber/pdfium comparison. Hyphens do not count: pdfium
    gives a soft hyphen at the end of a line as "-" or U+FFFE, pdfminer as U+00AD."""
    return sum(1 for c in text if not c.isspace() and c not in "-­￾\x02")


def _pdfium_counts(path: Path) -> list[int]:
    """Visible characters per page as pdfium reads them."""
    import pypdfium2

    doc = pypdfium2.PdfDocument(str(path))
    try:
        counts = []
        for i in range(len(doc)):
            page = doc[i]
            tp = page.get_textpage()
            counts.append(_glyphs(tp.get_text_range()))
            tp.close()
            page.close()
        return counts
    finally:
        doc.close()


# Symbol and Wingdings glyphs that a few files leave in the Unicode private use area
# (U+F000 + the font's own code): Symbol letters are Greek, the rest are bullets/signs.
_SYMBOL = dict(
    zip(
        range(0xF041, 0xF05B),
        "ΑΒΧΔΕΦΓΗΙϑΚΛΜΝΟΠΘΡΣΤΥςΩΞΨΖ",
        strict=True,
    )
)
_SYMBOL |= dict(zip(range(0xF061, 0xF07B), "αβχδεφγηιϕκλμνοπθρστυϖωξψζ", strict=True))
_SYMBOL |= {0xF020 + i: chr(0x20 + i) for i in range(0x00, 0x21) if chr(0x20 + i) not in "\"$'*-@"}
_SYMBOL |= {0xF022: "∀", 0xF024: "∃", 0xF02A: "∗", 0xF02D: "−", 0xF040: "≅", 0xF05B: "[", 0xF05D: "]",
            0xF05F: "_", 0xF07B: "{", 0xF07C: "|", 0xF07D: "}", 0xF07E: "∼", 0xF0A3: "≤", 0xF0A5: "∞",
            0xF0AE: "→", 0xF0B0: "°", 0xF0B1: "±", 0xF0B3: "≥", 0xF0B4: "×", 0xF0B7: "•", 0xF0B8: "÷",
            0xF0B9: "≠", 0xF0BB: "≈", 0xF0BC: "…", 0xF0D7: "⋅", 0xF0DE: "⇒", 0xF0E2: "®", 0xF0E3: "©",
            0xF0AB: "↔", 0xF0AC: "←", 0xF0AD: "↑", 0xF0AF: "↓", 0xF0D6: "√",
            0xF0E4: "™"}  # fmt: skip
_WINGDINGS = {0xF06C: "●", 0xF06E: "■", 0xF071: "❑", 0xF076: "❖", 0xF09F: "•", 0xF0A7: "▪", 0xF0A8: "◻",
              0xF0D8: "➢", 0xF0E0: "→", 0xF0E8: "➔", 0xF0F0: "⇨", 0xF0FC: "✓", 0xF0FB: "✗"}  # fmt: skip


def _unmap_private(text: str, chars: list[dict]) -> str:
    table: dict[int, str] = {}
    for c in chars:
        o = ord(c["text"][:1] or " ")
        if 0xF000 <= o <= 0xF0FF:
            font = c["fontname"].lower()
            mapped = _SYMBOL.get(o) if "symbol" in font else _WINGDINGS.get(o) if "wingding" in font else None
            if mapped:
                table[o] = mapped
    return text.translate(table) if table else text


def _open_pdf(path: Path):
    """pdfplumber on the file; pdfminer cannot read a few linearized files ("No /Root
    object!"), which pdfium (a pdfplumber dependency) reads: those are re-saved by pdfium
    into memory and read from there. The file on disk is not touched."""
    import io

    import pdfplumber  # optional dependency (pyproject extra "pdf")
    import pypdfium2
    from pdfplumber.utils.exceptions import PdfminerException

    pdf = None
    try:
        pdf = pdfplumber.open(path)
        pdf.pages  # noqa: B018 - parses the document
        return pdf
    except PdfminerException as e:
        if pdf is not None:
            pdf.close()
        if "/Root" not in str(e):
            raise
    buf = io.BytesIO()
    doc = pypdfium2.PdfDocument(str(path))
    try:
        doc.save(buf)
    finally:
        doc.close()
    buf.seek(0)
    return pdfplumber.open(buf)


def _line(p, r: dict) -> Line | None:
    text = clean(_unmap_private(r["text"], r["chars"]))
    chars = [c for c in r["chars"] if not c["text"].isspace()]
    if not text or not chars:
        return None
    bold = sum(1 for c in chars if _BOLD_FONT.search(c["fontname"].split("+")[-1]))
    italic = sum(1 for c in chars if _ITALIC_FONT.search(c["fontname"].split("+")[-1]))
    w1, x_end = 0.0, None  # the first word ends at a space glyph or at a gap (no glyph)
    for c in r["chars"]:
        gap = float(c["x0"]) - x_end if x_end is not None else 0.0
        nbsp = c["text"] == " "  # "19 ασθενείς" wraps as one word
        if (c["text"].isspace() and not nbsp) or gap > WORD_GAP * float(c["size"]):
            break
        x_end = float(c["x1"])
        w1 = x_end - float(r["chars"][0]["x0"])
    return Line(
        page=p.page_number,
        x0=round(float(r["x0"]), 1),
        x1=round(float(r["x1"]), 1),
        w1=round(w1, 1),
        top=round(float(r["top"]), 1),
        bottom=round(float(r["bottom"]), 1),
        size=round(statistics.median(float(c["size"]) for c in chars), 1),
        text=text,
        bold=bold >= 0.6 * len(chars),
        italic=italic >= 0.6 * len(chars),
    )


def is_table(cells: list[tuple[str, ...]]) -> bool:
    """A ruled grid is a table when at least two columns have text and most rows fill
    more than one cell. Text in a framed box is not (Levetiracetam Hospira frames whole
    pages; the hanging indent of its bullets makes a second "column")."""
    if len(cells) < 2:
        return False
    ncols = max(len(r) for r in cells)
    if sum(1 for k in range(ncols) if any(k < len(r) and r[k] for r in cells)) < 2:
        return False
    filled = [sum(1 for c in r if c) for r in cells]
    if sum(1 for n in filled if n == 1) >= 0.7 * sum(1 for n in filled if n):
        return False
    # a grid around SmPC headings is layout, not a table (Vitrakvi: 6.5 ... 10 in a grid)
    for r in cells:
        m = _SMPC_HEADING.match(next((c for c in r if c), ""))
        title = SMPC_TITLES.get((int(m.group(1)), int(m.group(2) or 0))) if m else None
        if title and similarity(m.group(4), title) >= 0.8:
            return False
    return True


def _tables(p):
    """(page without the table areas, table rows as Lines). A table is kept only when
    it has at least 2 rows and 2 columns and its cells hold every character of its area;
    otherwise its text is read as ordinary lines."""
    rows_out: list[Line] = []
    text_page = p
    for i, t in enumerate(p.find_tables(), 1):
        rows = t.extract(x_tolerance=1.5)
        ncols = max((len(r) for r in rows), default=0)
        if len(rows) < 2 or ncols < 2:
            continue
        cells = [tuple(_cell(c) for c in r) + ("",) * (ncols - len(r)) for r in rows]
        if not is_table(cells):
            continue
        x0, top, x1, bottom = t.bbox
        inside = [
            c
            for c in p.chars
            if x0 <= (c["x0"] + c["x1"]) / 2 <= x1 and top <= (c["top"] + c["bottom"]) / 2 <= bottom
        ]
        if sum(nonspace(c["text"]) for c in inside) != sum(nonspace(c) for r in cells for c in r):
            continue
        if not any(any(r) for r in cells):
            continue
        cells = [tuple(_unmap_private(c, inside) for c in r) for r in cells]
        sizes = [float(c["size"]) for c in inside] or [10.0]
        size = round(statistics.median(sizes), 1)
        for row, rect in zip(cells, t.rows, strict=False):
            if not any(row):
                continue
            rows_out.append(
                Line(
                    page=p.page_number,
                    x0=round(float(x0), 1),
                    x1=round(float(x1), 1),
                    top=round(float(rect.bbox[1]), 1),
                    bottom=round(float(rect.bbox[3]), 1),
                    size=size,
                    text=" ¦ ".join(row),
                    table=p.page_number * 100 + i,
                    cells=row,
                )
            )
        # a table may run past the page edge (EOF 2786801, 11/2026): not strict, so that
        # pdfplumber does not refuse an area partly outside the page
        text_page = text_page.outside_bbox(t.bbox, strict=False)
    return text_page, rows_out


def _cell(text: str | None) -> str:
    out = ""
    for part in (text or "").split("\n"):
        out = join_lines(out, clean(part))
    return out


# Debug / fixture format: "# page N WIDTH HEIGHT", then one line per text line:
# x0 <TAB> x1 <TAB> top <TAB> size <TAB> flags <TAB> text, flags "-", "B", "I", "BI", or
# "T<n>" for a row of table n with the cells separated by " ¦ ". bottom = top + size;
# the width of the first word is estimated from the text.


def lines_to_text(pages: Iterable[Page]) -> str:
    out = []
    for pg in pages:
        out.append(f"# page {pg.number} {pg.width:g} {pg.height:g}")
        for ln in pg.lines:
            flags = (
                f"T{ln.table}" if ln.table else ("B" if ln.bold else "") + ("I" if ln.italic else "") or "-"
            )
            out.append(f"{ln.x0:g}\t{ln.x1:g}\t{ln.top:g}\t{ln.size:g}\t{flags}\t{ln.text}")
    return "\n".join(out) + "\n"


def lines_from_text(text: str) -> list[Page]:
    pages: list[Page] = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        if raw.startswith("# page "):
            _, _, n, w, h = raw.split()
            pages.append(Page(int(n), float(w), float(h), []))
            continue
        if raw.startswith("#"):
            continue
        x0, x1, top, size, flags, body = raw.split("\t", 5)
        pg = pages[-1]
        common = dict(
            page=pg.number, x0=float(x0), x1=float(x1), top=float(top), bottom=float(top) + float(size)
        )
        if flags.startswith("T"):
            cells = tuple(c.strip() for c in body.split("¦"))
            pg.lines.append(Line(**common, size=float(size), text=body, table=int(flags[1:]), cells=cells))
        else:
            pg.lines.append(
                Line(**common, size=float(size), text=clean(body), bold="B" in flags, italic="I" in flags)
            )
    return pages


# --- page furniture ----------------------------------------------------------------

_PAGE_NO = re.compile(rf"^(?:{_fold('ΣΕΛΙΔΑ')}\s*)?\d{{1,4}}(?:\s*(?:/|{_fold('ΑΠΟ')})\s*\d{{1,4}})?$")


def strip_furniture(pages: list[Page]) -> tuple[list[Page], Counter]:
    """Remove page numbers and running headers/footers: a line in the top 6% or bottom
    7.5% of the page whose text (digits ignored) appears there on at least 3 pages and
    at least 20% of all pages. Returns the pages and the removed texts with counts."""

    def zone(pg: Page, ln: Line) -> bool:
        return ln.top < 0.06 * pg.height or ln.top > 0.925 * pg.height

    def key(ln: Line) -> str:
        return re.sub(r"\d+", "#", fold(ln.text))

    seen: Counter = Counter()
    for pg in pages:
        seen.update({key(ln) for ln in pg.lines if not ln.table and zone(pg, ln)})
    need = max(3, 0.2 * len(pages))
    removed: Counter = Counter()
    out = []
    for pg in pages:
        keep = []
        for ln in pg.lines:
            if not ln.table and zone(pg, ln):
                is_number = ln.top > 0.9 * pg.height and _PAGE_NO.match(fold(ln.text))
                if is_number or seen[key(ln)] >= need:
                    removed[key(ln)] += 1
                    continue
            keep.append(ln)
        out.append(Page(pg.number, pg.width, pg.height, keep, pg.chars_read, pg.chars_pdfium))
    return out, removed


# --- parts of the PDF --------------------------------------------------------------


def _phrase(text: str) -> str:
    """Regex for a phrase on folded text: words as written, any spacing, optional dots."""
    return r"\s*".join(re.escape(w).replace(r"\.", r"\.?") for w in fold(text).split())


_PIL_WHO = rf"(?:{_phrase('ΧΡΗΣΗΣ')}|{_phrase('ΓΙΑ ΤΟΝ ΧΡΗΣΤΗ')}|{_phrase('ΓΙΑ ΤΟ ΧΡΗΣΤΗ')})"
_COVERS = [
    ("annex", re.compile(rf"^{_phrase('ΠΑΡΑΡΤΗΜΑ')}\s*(IV|III|II|I)$")),
    # "A."/"B." may be missing, or swapped (one file has "A. ΦΥΛΛΟ ΟΔΗΓΙΩΝ ΧΡΗΣΗΣ")
    ("labelling", re.compile(rf"^(?:[AB]?\.?\s*)?{_phrase('ΕΠΙΣΗΜΑΝΣΗ')}$")),
    ("pil", re.compile(rf"^(?:[AB]?\.?\s*)?{_phrase('ΦΥΛΛΟ ΟΔΗΓΙΩΝ')}\s*{_PIL_WHO}$")),
]
_ANNEX_PART = {"I": "smpc", "II": "annex_ii", "III": "annex_iii", "IV": "annex_iv"}
PARTS = ("front", "smpc", "annex_ii", "annex_iii", "labelling", "pil", "annex_iv")


_COVER_TITLES = [("labelling", fold("Α. ΕΠΙΣΗΜΑΝΣΗ")), ("pil", fold("Β. ΦΥΛΛΟ ΟΔΗΓΙΩΝ ΧΡΗΣΗΣ"))]


def cover_of(pg: Page) -> tuple[str, int] | None:
    """(part, index of its title line) when the page is a divider, else None. A divider
    has few lines and its title ("ΠΑΡΑΡΤΗΜΑ Ι", "Α. ΕΠΙΣΗΜΑΝΣΗ", ...) below the top fifth
    of the page; a few files carry a note above it ("Το παρόν έγγραφο αποτελεί ...")."""
    body = [ln for ln in pg.lines if not ln.table]
    if not body or len(body) > 15:
        return None
    for k, ln in enumerate(body):
        if ln.top < 0.2 * pg.height:
            continue
        first = fold(ln.text)
        for kind, rx in _COVERS:
            m = rx.match(first)
            if m:
                return (_ANNEX_PART[m.group(1)] if kind == "annex" else kind), k
        if len(body) - k <= 2:  # a divider with a damaged title: "B. ΦΥΛ039BΟ ΟΔΗΓΙΩΝ ΧΡΗΣΗΣ"
            for kind, title in _COVER_TITLES:
                if difflib.SequenceMatcher(None, first, title).ratio() >= 0.8:
                    return kind, k
        break
    return None


def split_parts(pages: list[Page]) -> tuple[dict[str, list[int]], dict[str, list[Line]], set[int]]:
    """({part: [first page, last page]}, {part: lines without the divider pages},
    divider page numbers). Parts in PDF order: smpc (Annex I), annex_ii, annex_iii
    (its own divider only), labelling (III A), pil (III B), annex_iv; "front" is what
    comes before the first divider (lines above a divider title stay with the part
    before it)."""
    ranges: dict[str, list[int]] = {}
    lines: dict[str, list[Line]] = {}
    covers: set[int] = set()
    part = "front"
    for pg in pages:
        c = cover_of(pg)
        body = pg.lines
        if c:
            above = [ln for ln in pg.lines if not ln.table][: c[1]]
            if above:
                if part not in ranges:
                    ranges[part] = [pg.number, pg.number]
                ranges[part][1] = pg.number
                lines.setdefault(part, []).extend(above)
            if c[0] in ranges and c[0] != part:  # the same part again (Nuwiq: two labellings) is fine
                raise ParseError(f"part {c[0]} opens twice (pages {ranges[c[0]][0]} and {pg.number})")
            part = c[0]
            covers.add(pg.number)
            body = []
        if part not in ranges:
            if part == "front" and not body:
                continue
            ranges[part] = [pg.number, pg.number]
        ranges[part][1] = pg.number
        lines.setdefault(part, []).extend(body)
    return ranges, lines, covers


class ParseError(ValueError):
    """The PDF does not have the structure we read; the file is reported, not written."""


# --- headings ----------------------------------------------------------------------

SMPC_TITLES: dict[tuple[int, int], str] = {
    (1, 0): "ΟΝΟΜΑΣΙΑ ΤΟΥ ΦΑΡΜΑΚΕΥΤΙΚΟΥ ΠΡΟΪΟΝΤΟΣ",
    (2, 0): "ΠΟΙΟΤΙΚΗ ΚΑΙ ΠΟΣΟΤΙΚΗ ΣΥΝΘΕΣΗ",
    (3, 0): "ΦΑΡΜΑΚΟΤΕΧΝΙΚΗ ΜΟΡΦΗ",
    (4, 0): "ΚΛΙΝΙΚΕΣ ΠΛΗΡΟΦΟΡΙΕΣ",
    (4, 1): "Θεραπευτικές ενδείξεις",
    (4, 2): "Δοσολογία και τρόπος χορήγησης",
    (4, 3): "Αντενδείξεις",
    (4, 4): "Ειδικές προειδοποιήσεις και προφυλάξεις κατά τη χρήση",
    (4, 5): "Αλληλεπιδράσεις με άλλα φαρμακευτικά προϊόντα και άλλες μορφές αλληλεπίδρασης",
    (4, 6): "Γονιμότητα, κύηση και γαλουχία",
    (4, 7): "Επιδράσεις στην ικανότητα οδήγησης και χειρισμού μηχανημάτων",
    (4, 8): "Ανεπιθύμητες ενέργειες",
    (4, 9): "Υπερδοσολογία",
    (5, 0): "ΦΑΡΜΑΚΟΛΟΓΙΚΕΣ ΙΔΙΟΤΗΤΕΣ",
    (5, 1): "Φαρμακοδυναμικές ιδιότητες",
    (5, 2): "Φαρμακοκινητικές ιδιότητες",
    (5, 3): "Προκλινικά δεδομένα για την ασφάλεια",
    (6, 0): "ΦΑΡΜΑΚΕΥΤΙΚΕΣ ΠΛΗΡΟΦΟΡΙΕΣ",
    (6, 1): "Κατάλογος εκδόχων",
    (6, 2): "Ασυμβατότητες",
    (6, 3): "Διάρκεια ζωής",
    (6, 4): "Ιδιαίτερες προφυλάξεις κατά τη φύλαξη του προϊόντος",
    (6, 5): "Φύση και συστατικά του περιέκτη",
    (6, 6): "Ιδιαίτερες προφυλάξεις απόρριψης και άλλος χειρισμός",
    (7, 0): "ΚΑΤΟΧΟΣ ΤΗΣ ΑΔΕΙΑΣ ΚΥΚΛΟΦΟΡΙΑΣ",
    (8, 0): "ΑΡΙΘΜΟΣ(ΟΙ) ΑΔΕΙΑΣ ΚΥΚΛΟΦΟΡΙΑΣ",
    (9, 0): "ΗΜΕΡΟΜΗΝΙΑ ΠΡΩΤΗΣ ΕΓΚΡΙΣΗΣ/ΑΝΑΝΕΩΣΗΣ ΤΗΣ ΑΔΕΙΑΣ",
    (10, 0): "ΗΜΕΡΟΜΗΝΙΑ ΑΝΑΘΕΩΡΗΣΗΣ ΤΟΥ ΚΕΙΜΕΝΟΥ",
    (11, 0): "ΔΟΣΙΜΕΤΡΙΑ",
    (12, 0): "ΟΔΗΓΙΕΣ ΓΙΑ ΤΗΝ ΠΑΡΑΣΚΕΥΗ ΡΑΔΙΟΦΑΡΜΑΚΩΝ",
}
_SMPC_ORDER = sorted(SMPC_TITLES)
SMPC_REQUIRED = ("4.1", "4.2", "4.3", "4.4", "4.5", "4.6", "4.8")
SMPC_EXPECTED = tuple(f"{a}.{b}" if b else str(a) for a, b in _SMPC_ORDER if a <= 10)

# PIL section n: (number, what its title must contain once folded)
PIL_TITLES = {
    1: ("Τι είναι",),
    2: ("Τι πρέπει να γνωρίζετε", "Πριν "),
    3: ("Πώς ", "Πως "),
    4: ("ανεπιθύμητες",),
    5: ("φυλάσσ", "φύλαξη", "φυλαχτ", "φυλάγ"),
    6: ("Περιεχόμεν", "λοιπές πληροφορίες"),
}


def _squash(text: str) -> str:
    """Doubled letters as one: "φυλλάσετε" (a typo in one leaflet) reads as "φυλάσσετε"."""
    return re.sub(r"(.)\1+", r"\1", text)


_PIL_KEYS = {n: tuple(_squash(fold(k)) for k in keys) for n, keys in PIL_TITLES.items()}
PIL_EXPECTED = tuple(str(n) for n in PIL_TITLES)

# "4.1 Θεραπευτικές ενδείξεις", "1. ΟΝΟΜΑΣΙΑ ...", and "1 ΟΝΟΜΑΣΙΑ ..." (no dot: group 3 empty)
_SMPC_HEADING = re.compile(r"^(\d{1,2})(?:\.(\d{1,2})\.?|(\.?))\s*([^\W\d_].*)$")
_PIL_HEADING = re.compile(r"^([1-6])\.?\s+([^\W\d_].*)$")
# "Φύλλο οδηγιών χρήσης: Πληροφορίες ...", also "Φυλλο οδηγιων: Πληροφοριες για το χρηστη" (Ebixa)
# (not "φύλλο οδηγιών χρήσης. Πρέπει να ..." in a sentence: see _is_pil_title)
_PIL_TITLE = re.compile(rf"^{_phrase('ΦΥΛΛΟ ΟΔΗΓΙΩΝ')}\s*(?:{_PIL_WHO}\s*(?::|$|{_phrase('ΠΛΗΡΟΦΟΡΙΕΣ')})|:)")


def _is_pil_title(ln: Line) -> bool:
    return (
        not ln.table
        and ln.bold
        and len(ln.text) < 100
        and ln.text[:1].isupper()
        and bool(_PIL_TITLE.match(fold(ln.text)))
    )


# "Τι περιέχει το παρόν φύλλο οδηγιών", "Τι περιέχει αυτό το φυλλάδιο οδηγιών"
_PIL_TOC = (fold("ΟΔΗΓΙ"), fold("ΠΕΡΙΕΧ"))
_TOC_ENTRY = re.compile(r"^\d{1,2}\.?\s")  # "1. Τι είναι ..." or "1 Τι είναι ..."


def _num_str(num: tuple[int, int]) -> str:
    return f"{num[0]}.{num[1]}" if num[1] else str(num[0])


def similarity(title: str, canonical: str) -> float:
    """How close a heading title is to the QRD title. A title of at least half the QRD
    length is compared with the same length of it (the first line of a wrapped title);
    a shorter one with the whole ("ημέρες/μήνα." is not "ΗΜΕΡΟΜΗΝΙΑ ΑΝΑΘΕΩΡΗΣΗΣ ...")."""
    a, b = fold(title), fold(canonical)
    if 2 * len(a) >= len(b) and len(a) < len(b):
        b = b[: len(a)]
    return difflib.SequenceMatcher(None, a, b).ratio()


def _capitals(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and sum(1 for c in letters if c.isupper()) >= 0.8 * len(letters)


def _next_smpc(last: tuple[int, int] | None) -> tuple[int, int] | None:
    for k in _SMPC_ORDER:
        if last is None or k > last:
            return k
    return None


# --- documents ---------------------------------------------------------------------


@dataclass
class Section:
    number: str
    title: str
    page: int
    lines: list[Line] = field(default_factory=list)


@dataclass
class Doc:
    kind: str  # "smpc" or "pil"
    first_page: int
    title: str = ""
    title_lines: list[Line] = field(default_factory=list)
    preamble: list[Line] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    toc: int = 0  # PIL: entries of "Τι περιέχει το παρόν φύλλο οδηγιών" seen
    toc_open: bool = False

    def sink(self) -> list[Line]:
        return self.sections[-1].lines if self.sections else self.preamble


@dataclass
class Tally:
    """Characters in (lines read) and out (characters written, plus markers removed)."""

    lines_in: int = 0
    markers: int = 0
    right: float = 0.0  # right edge of the text column, to tell intended line breaks
    notes: list[str] = field(default_factory=list)  # for the warnings


def _take_title(lines: list[Line], i: int, num_title: str, canonical: str | None) -> tuple[str, int]:
    """Heading title from line i plus wrapped bold lines right below it."""
    ln, title, j = lines[i], num_title, i + 1
    while j < len(lines) and j - i <= 2:
        nx = lines[j]
        if nx.table or nx.page != ln.page or not ln.bold or not nx.bold:
            break
        if nx.top - lines[j - 1].bottom > 0.5 * ln.size or nx.x0 < ln.x0 - 2:
            break
        if _SMPC_HEADING.match(nx.text) or _PIL_HEADING.match(nx.text):
            break
        if canonical is not None and len(fold(title)) >= len(fold(canonical)) - 2:
            break
        title, j = join_lines(title, nx.text), j + 1
    return title, j


def _move_page_preamble(prev: Doc, doc: Doc, page: int) -> None:
    """A new document starts on a new page: lines of that page that the previous
    document's last section collected before the new heading belong to the new one."""
    sink = prev.sink()
    k = len(sink)
    while k > 0 and sink[k - 1].page == page:
        k -= 1
    if k < len(sink) and k > 0:
        doc.preamble[:0] = sink[k:]
        del sink[k:]


def parse_smpc(lines: list[Line], tally: Tally) -> list[Doc]:
    docs: list[Doc] = []
    last: tuple[int, int] | None = None
    i = 0
    while i < len(lines):
        ln = lines[i]
        if not docs:
            docs.append(Doc("smpc", ln.page))
        m = None if ln.table else _SMPC_HEADING.match(ln.text)
        nxt = _next_smpc(last)
        if m:
            num = (int(m.group(1)), int(m.group(2) or 0))
            head = m.group(4)
            canonical = SMPC_TITLES.get(num)
            sim = similarity(head, canonical) if canonical else 0.0
            dotless = m.group(3) == ""  # "1 ΟΝΟΜΑΣΙΑ ...": only with its QRD title ("1 ml ..." is not)
            action = None
            if num == (1, 0) and last is not None and last >= (7, 0) and sim >= 0.7:
                action = "new"
            elif last is None or num > last:
                # bold and the next number is enough for a subsection; a main section also
                # needs capitals (QRD: "7. ΚΑΤΟΧΟΣ ..."), not "7. Ελέγξτε για φυσαλίδες" in 6.6
                next_ok = ln.bold and not dotless and num == nxt and (num[1] or _capitals(head))
                if canonical and (sim >= 0.6 or next_ok):
                    action = "section"
                elif not canonical and ln.bold and not dotless and last and len(head) >= 8:
                    # beyond the QRD list, only the next one ("6.7" after "6.6"), not "5.4 RM" in 5.1
                    if num == (last[0], last[1] + 1) or num == (last[0] + 1, 0):
                        action = "section"
            if not action and ln.bold and nxt and last and similarity(head, SMPC_TITLES[nxt]) >= 0.8:
                # misnumbered in the PDF (Renvela: "6.2 Κατάλογος εκδόχων" for 6.1)
                tally.notes.append(f"SmPC {len(docs)}: heading {ln.text[:50]!r} read as {_num_str(nxt)}")
                num, canonical, action = nxt, SMPC_TITLES[nxt], "section"
            if action:
                if action == "new":
                    prev = docs[-1]
                    docs.append(Doc("smpc", ln.page))
                    _move_page_preamble(prev, docs[-1], ln.page)
                title, j = _take_title(lines, i, head, canonical)
                tally.lines_in += sum(nonspace(x.text) for x in lines[i:j])
                tally.markers += nonspace(ln.text) - nonspace(head) - nonspace(_num_str(num))
                docs[-1].sections.append(Section(_num_str(num), title, ln.page))
                last = num
                i = j
                continue
        elif (
            nxt
            and last
            and ln.bold
            and not ln.table
            and difflib.SequenceMatcher(None, fold(ln.text), fold(SMPC_TITLES[nxt])).ratio() >= 0.9
        ):
            # the title of the next section without its number (Mysimba: "Ασυμβατότητες")
            tally.lines_in += nonspace(ln.text)
            tally.markers -= nonspace(_num_str(nxt))  # a number that is not in the PDF
            tally.notes.append(
                f"SmPC {len(docs)}: heading {ln.text[:50]!r} without number read as {_num_str(nxt)}"
            )
            docs[-1].sections.append(Section(_num_str(nxt), ln.text, ln.page))
            last = nxt
            i += 1
            continue
        tally.lines_in += _line_chars(ln)
        docs[-1].sink().append(ln)
        i += 1
    return docs


def _pil_keyword(n: int, title: str) -> bool:
    t = _squash(fold(title))
    keys = _PIL_KEYS[n]
    if n in (1, 3):
        return any(t.startswith(k.strip()) for k in keys)
    return any(k.strip() in t for k in keys)


def _starts_contents_list(lines: list[Line], i: int) -> bool:
    """Six numbered lines in a row, close together, the first "... Τι είναι ...": the
    contents list of a leaflet, even without its title."""
    run = lines[i : i + 6]
    if len(run) < 6 or any(ln.table or not _TOC_ENTRY.match(ln.text) for ln in run):
        return False
    if not _pil_keyword(1, _TOC_ENTRY.sub("", run[0].text, count=1)):
        return False
    return all(
        b.page == a.page and b.top - a.bottom < 0.5 * b.size for a, b in zip(run, run[1:], strict=False)
    )


def parse_pil(lines: list[Line], tally: Tally) -> list[Doc]:
    docs: list[Doc] = []
    last = 0
    i = 0
    while i < len(lines):
        ln = lines[i]
        if _is_pil_title(ln):
            if not docs or docs[-1].sections or docs[-1].title:
                prev = docs[-1] if docs else None
                docs.append(Doc("pil", ln.page))
                if prev:
                    _move_page_preamble(prev, docs[-1], ln.page)
                last = 0
            docs[-1].title = ln.text
            docs[-1].title_lines.append(ln)
            tally.lines_in += nonspace(ln.text)
            i += 1
            continue
        if not docs:
            docs.append(Doc("pil", ln.page))
        doc = docs[-1]
        prev_ln = lines[i - 1] if i else None
        if not ln.table and ((not doc.sections and not doc.toc) or last == 6):
            toc_title = len(ln.text) < 80 and all(k in fold(ln.text) for k in _PIL_TOC)
            toc_title = toc_title and (ln.bold or not doc.sections)  # in a section 6, only a bold title
            if toc_title or _starts_contents_list(lines, i):  # with its title or without
                if doc.sections:  # after section 6: the contents list of the next leaflet
                    prev = doc
                    docs.append(Doc("pil", ln.page))
                    _move_page_preamble(prev, docs[-1], ln.page)
                    doc, last = docs[-1], 0
                doc.toc_open = True
        if doc.toc_open and not ln.table:
            # The contents list: the numbered lines under its title, up to the first blank
            # line. Their numbers are not trusted (some files print 7-12 or 8-14).
            gap = ln.top - prev_ln.bottom if prev_ln and prev_ln.page == ln.page else ln.size
            if doc.toc and gap > 0.5 * ln.size:
                doc.toc_open = False
            elif _TOC_ENTRY.match(ln.text):
                doc.toc += 1
                tally.lines_in += _line_chars(ln)
                doc.sink().append(ln)
                i += 1
                continue
        m = None if ln.table else _PIL_HEADING.match(ln.text)
        if m:
            n, title = int(m.group(1)), m.group(2)
            take = 0  # the section this line opens
            if _pil_keyword(n, title):
                if n == 1 and last == 6:
                    prev = doc
                    docs.append(Doc("pil", ln.page))
                    _move_page_preamble(prev, docs[-1], ln.page)
                    doc, last, take = docs[-1], 0, 1
                elif n > last and (ln.bold or n == last + 1):
                    take = n
            elif ln.bold and not doc.toc_open and last < 6 and _pil_keyword(last + 1, title):
                take = last + 1  # misnumbered in the PDF ("2. Τι είναι ..." for section 1)
                tally.notes.append(f"PIL {len(docs)}: heading {ln.text[:50]!r} read as section {take}")
            if take:
                doc.toc_open = False
                full, j = _take_title(lines, i, title, None)
                tally.lines_in += sum(nonspace(x.text) for x in lines[i:j])
                tally.markers += nonspace(ln.text) - nonspace(title) - len(str(take))
                doc.sections.append(Section(str(take), full, ln.page))
                last = take
                i = j
                continue
        tally.lines_in += _line_chars(ln)
        doc.sink().append(ln)
        i += 1
    return docs


def _line_chars(ln: Line) -> int:
    return sum(nonspace(c) for c in ln.cells) if ln.table else nonspace(ln.text)


# --- blocks ------------------------------------------------------------------------

# "*" is left out: under tables it marks a footnote ("* μέγιστη δόση ..."), not a bullet
_BULLET = re.compile(r"^([•●▪■◦○·∙\-–−‣⁃➢✓❖❑◻□◆♦\uf0b7\uf0a7\uf0d8\uf0a8])\s*(?=\S)")
_NUMBERED = re.compile(r"^\(?(?:\d{1,2}|[α-ωa-z]|[ivx]{1,4})[.)]\s")
_TERMINAL = ".:;!?»"


def _bullet(text: str) -> re.Match | None:
    m = _BULLET.match(text)
    if not m:
        return None
    rest = text[m.end() :]
    if m.group(1) in "-–−" and (rest[:1].isdigit() or text[1:2] != " "):
        return None  # "-5 °C", "–20%": a sign, not a bullet
    return m


def blocks(lines: list[Line], tally: Tally) -> list[dict]:
    """Paragraphs, sub-headings, bullet lists and tables, in order."""
    out: list[dict] = []
    cur: dict | None = None  # block being built
    prev: Line | None = None
    for ln in lines:
        if ln.table:
            row = list(ln.cells)
            same = cur is not None and cur["type"] == "table" and cur["_id"] == ln.table
            # a table that goes on at the top of the next page, with nothing in between
            goes_on = (
                cur is not None
                and cur["type"] == "table"
                and prev is not None
                and ln.page == prev.page + 1
                and len(cur["rows"][0]) == len(row)
            )
            if same or goes_on:
                if not same:
                    cur["_id"] = ln.table
                    if row == cur["rows"][0]:  # header row repeated on the new page
                        tally.markers += _line_chars(ln)
                        prev = ln
                        continue
                cur["rows"].append(row)
            else:
                cur = {"type": "table", "rows": [row], "_id": ln.table}
                out.append(cur)
            prev = ln
            continue
        b = _bullet(ln.text)
        if b:
            text = ln.text[b.end() :]
            tally.markers += nonspace(b.group(1))
            if cur and cur["type"] == "list" and abs(ln.x0 - cur["_x"]) < 3:
                cur["items"].append(text)
            else:
                level = 2 if cur and cur["type"] == "list" and ln.x0 > cur["_x"] + 3 else 1
                if cur and cur["type"] == "list" and ln.x0 < cur["_x"] - 3:
                    level = 1
                cur = {"type": "list", "level": level, "items": [text], "_x": ln.x0, "_style": ""}
                out.append(cur)
            prev = ln
            continue
        if cur is None or prev is None or prev.table or _starts_block(prev, ln, cur, tally.right):
            cur = {"type": "paragraph", "text": ln.text, "_style": _style(ln)}
            out.append(cur)
            prev = ln
            continue
        key = "items" if cur["type"] == "list" else "text"
        text = cur["items"][-1] if key == "items" else cur["text"]
        if _hard_break(prev, ln, tally.right):
            text = f"{text}\n{ln.text}"
        else:
            text = join_lines(text, ln.text)
        if key == "items":
            cur["items"][-1] = text
        else:
            cur["text"] = text
        if cur["_style"] != _style(ln):
            cur["_style"] = ""
        prev = ln
    for blk in out:
        style = blk.pop("_style", "")
        blk.pop("_x", None)
        blk.pop("_id", None)
        if blk["type"] == "paragraph" and style and len(blk["text"]) <= 200 and blk["text"][-1] not in ".;":
            blk["type"] = "subheading"
    return out


def _style(ln: Line) -> str:
    return "bold" if ln.bold else "italic" if ln.italic else ""


def _hard_break(prev: Line, ln: Line, right: float) -> bool:
    """The line ends early although the next word would have fitted: a break the
    author made (addresses, lists of excipients, EU/1/... numbers)."""
    if not right or prev.page != ln.page or prev.table or ln.table:
        return False
    return prev.x1 + 0.3 * prev.size + ln.first_word_width() < right - 0.5 * prev.size


def _starts_block(prev: Line, ln: Line, cur: dict, right: float) -> bool:
    last = (cur["items"][-1] if cur["type"] == "list" else cur["text"]).rstrip()
    numbered = _NUMBERED.match(ln.text) is not None
    if ln.page != prev.page:
        if _style(ln) != _style(prev) or not last or last[-1] in _TERMINAL or numbered:
            return True
        if cur["type"] == "list":
            return not (ln.x0 > cur["_x"] + 3 or ln.text[:1].islower())
        return ln.text[:1].isupper()
    if ln.top - prev.bottom > 0.5 * max(ln.size, prev.size):
        return True
    if _style(ln) != _style(prev):
        return True
    if numbered and (last[-1:] in tuple(_TERMINAL) or _hard_break(prev, ln, right)):
        return True
    if cur["type"] == "list" and ln.x0 <= cur["_x"] + 1 and last[-1:] in tuple(_TERMINAL):
        return True
    return False


def _doc_json(doc: Doc, tally: Tally) -> dict:
    out: dict = {"type": doc.kind}
    if doc.kind == "pil":
        out["title"] = doc.title
    pages = [ln.page for ln in doc.title_lines + doc.preamble] + [
        p for s in doc.sections for p in [s.page] + [ln.page for ln in s.lines]
    ]
    out["pages"] = [min(pages), max(pages)] if pages else [doc.first_page, doc.first_page]
    out["products"] = []
    out["preamble"] = blocks(doc.preamble, tally)
    out["sections"] = [
        {"number": s.number, "title": s.title, "page": s.page, "blocks": blocks(s.lines, tally)}
        for s in doc.sections
    ]
    out["products"] = _products(doc, out)
    return out


def _products(doc: Doc, out: dict) -> list[str]:
    """SmPC: the names in section 1, one per line. PIL: the bold lines under the title."""
    names: list[str] = []
    if doc.kind == "smpc":
        sec = next((s for s in out["sections"] if s["number"] == "1"), None)
        for b in sec["blocks"] if sec else []:
            if b["type"] == "list":
                names.extend(b["items"])
            elif b["type"] != "table":
                names.extend(t for t in b["text"].split("\n") if t)
        return names
    if not doc.title_lines:
        return names
    prev = doc.title_lines[-1]
    after = [ln for ln in doc.preamble if (ln.page, ln.top) > (prev.page, prev.top)]
    for ln in after:
        gap = ln.top - prev.bottom
        if ln.table or not ln.bold or ln.page != prev.page or gap > (0.6 if names else 3) * ln.size:
            break
        if names and ln.text[:1].islower():
            names[-1] = join_lines(names[-1], ln.text)
        else:
            names.append(ln.text)
        prev = ln
    return names


def _chars_out(obj) -> int:
    if isinstance(obj, str):
        return nonspace(obj)
    if isinstance(obj, list):
        return sum(_chars_out(x) for x in obj)
    if isinstance(obj, dict):
        return sum(
            _chars_out(v)
            for k, v in obj.items()
            if k in ("number", "title", "text", "items", "rows", "preamble", "sections", "blocks")
        )
    return 0


# --- one file ----------------------------------------------------------------------


def _right_edge(lines: list[Line]) -> float:
    """Where full lines end: the 90th percentile of the line ends (0 when too few)."""
    ends = sorted(ln.x1 for ln in lines if not ln.table)
    return ends[int(0.9 * (len(ends) - 1))] if len(ends) >= 20 else 0.0


def parse_pages(pages: list[Page]) -> dict:
    """{"parts", "smpc", "pil", "warnings", "stats"} or ParseError."""
    if not any(pg.lines for pg in pages):
        raise ParseError("no text in the PDF (scanned or unreadable fonts)")
    read = sum(pg.chars_read for pg in pages if pg.chars_pdfium is not None)
    expected = sum(pg.chars_pdfium or 0 for pg in pages)
    if expected and read < 0.999 * expected:
        raise ParseError(
            f"characters missing from the PDF text layer: {read} read of {expected} "
            "(fonts that pdfminer cannot decode)"
        )
    share = greek_share(ln.text for pg in pages for ln in pg.lines)
    if share < 0.5:
        raise ParseError(f"the PDF is not in Greek ({share:.0%} of the letters are Greek)")
    pages, removed = strip_furniture(pages)
    ranges, part_lines, _ = split_parts(pages)
    warnings: list[str] = []
    front = part_lines.get("front", [])
    if "smpc" not in ranges and front and _SMPC_HEADING.match(front[0].text):
        head = _SMPC_HEADING.match(front[0].text)
        if head.group(1) == "1" and similarity(head.group(4), SMPC_TITLES[(1, 0)]) >= 0.8:
            # no "ΠΑΡΑΡΤΗΜΑ Ι" page: the file starts with section 1 of the SmPC (Libmyris)
            ranges["smpc"], part_lines["smpc"] = ranges.pop("front"), part_lines.pop("front")
            warnings.append("no Annex I divider page: the SmPC starts on page 1")
    for need in ("smpc", "pil"):
        if need not in ranges:
            raise ParseError(
                f"no {'Annex I divider' if need == 'smpc' else 'Annex III B divider'} page found"
            )
    order = [p for p in PARTS if p in ranges]
    if order != sorted(order, key=lambda p: ranges[p][0]):
        raise ParseError(f"parts out of order: {[(p, ranges[p]) for p in order]}")

    tally = Tally()
    front_lines = part_lines.get("front", [])
    tally.lines_in += sum(_line_chars(ln) for ln in front_lines)
    front = blocks(front_lines, tally)
    tally.right = _right_edge(part_lines.get("smpc", []))
    smpc = [_doc_json(d, tally) for d in parse_smpc(part_lines.get("smpc", []), tally)]
    tally.right = _right_edge(part_lines.get("pil", []))
    pil = [_doc_json(d, tally) for d in parse_pil(part_lines.get("pil", []), tally)]
    written = _chars_out(front) + _chars_out(smpc) + _chars_out(pil) + tally.markers
    if written != tally.lines_in:
        raise ParseError(f"internal: {tally.lines_in} characters read, {written} written")

    errors: list[str] = []
    if not smpc or not any(d["sections"] for d in smpc):
        unnumbered = fold(SMPC_TITLES[(1, 0)])
        if any(fold(ln.text) == unnumbered for ln in part_lines.get("smpc", [])):
            errors.append("SmPC: headings without their numbers (characters missing from the PDF text layer)")
        else:
            errors.append("SmPC: no numbered sections found")
    if not pil or not any(d["sections"] for d in pil):
        errors.append("PIL: no numbered sections found")
    for k, d in enumerate(smpc, 1):
        found = [s["number"] for s in d["sections"]]
        if d["sections"] or len(smpc) == 1:
            miss = [n for n in SMPC_REQUIRED if n not in found]
            if miss:
                errors.append(f"SmPC {k} (p. {d['pages'][0]}): sections not found: {', '.join(miss)}")
            other = [n for n in SMPC_EXPECTED if n not in found and n not in SMPC_REQUIRED]
            if other:
                warnings.append(f"SmPC {k}: sections not found: {', '.join(other)}")
        elif d["preamble"]:
            errors.append(f"SmPC {k} (p. {d['pages'][0]}): text without sections")
    for k, d in enumerate(pil, 1):
        found = [s["number"] for s in d["sections"]]
        miss = [n for n in PIL_EXPECTED if n not in found]
        if miss:
            errors.append(f"PIL {k} (p. {d['pages'][0]}): sections not found: {', '.join(miss)}")
        elif not any(s["blocks"] for s in d["sections"]):
            errors.append(
                f"PIL {k} (p. {d['pages'][0]}): all sections empty (contents list read as headings?)"
            )
    for kind, docs in (("SmPC", smpc), ("PIL", pil)):
        for k, d in enumerate(docs, 1):
            secs = d["sections"]
            for a, b in zip(secs, [*secs[1:], None], strict=False):
                parent = b is not None and b["number"].startswith(a["number"] + ".")
                if not a["blocks"] and not parent:
                    warnings.append(f"{kind} {k}: section {a['number']} is empty")
    if errors:
        raise ParseError("; ".join(errors))
    warnings.extend(tally.notes)
    return {
        "parts": {p: ranges[p] for p in order},
        "front": front,
        "smpc": smpc,
        "pil": pil,
        "warnings": warnings,
        "stats": {"furniture_removed": sum(removed.values()), "characters": tally.lines_in},
    }


def parse_file(entry: dict, cache: Path) -> dict:
    """The product JSON for one manifest entry (ParseError when the PDF does not parse)."""
    path = cache / entry["file"]
    pages = read_pdf(path)
    res = parse_pages(pages)
    stamp = datetime.strptime(entry["retrieved_at"], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    return {
        "schema_version": SCHEMA_VERSION,
        "product_number": entry["product_number"],
        "name": entry["name"],
        "language": "el",
        "source": {
            "publisher": PUBLISHER,
            "url": entry["url"],
            "sha256": entry["sha256"],
            "retrieved_at": stamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "last_modified": entry.get("last_modified", ""),
            "pages": len(pages),
            "attribution": ATTRIBUTION,
        },
        "parts": res["parts"],
        "front": res["front"],
        "smpc": res["smpc"],
        "pil": res["pil"],
        "warnings": res["warnings"],
    }


def _parse_job(args: tuple[dict, str]) -> tuple[str, dict | None, str]:
    entry, cache = args
    try:
        return entry["product_number"], parse_file(entry, Path(cache)), ""
    except ParseError as e:
        return entry["product_number"], None, str(e)
    except Exception as e:  # a broken PDF must not stop the run; it is reported
        return entry["product_number"], None, f"{type(e).__name__}: {e}"


def parse_all(cache: Path, out: Path, jobs: int = 1, only: Iterable[str] | None = None, log=print) -> dict:
    entries = current_entries(cache)
    if only:
        entries = {k: v for k, v in entries.items() if k in set(only)}
    out.mkdir(parents=True, exist_ok=True)
    report: dict = {"generated": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "parsed": [], "failed": []}
    todo = [(entries[k], str(cache)) for k in sorted(entries)]
    written: set[str] = set()
    results: Iterable
    if jobs > 1:
        pool = ProcessPoolExecutor(jobs)
        results = pool.map(_parse_job, todo, chunksize=1)
    else:
        pool = None
        results = map(_parse_job, todo)
    try:
        for i, (number, doc, why) in enumerate(results, 1):
            e = entries[number]
            if doc is None:
                report["failed"].append(
                    {"product_number": number, "name": e["name"], "file": e["file"], "reason": why}
                )
                log(f"[{i}/{len(todo)}] FAILED {number} {e['name']}: {why}")
                continue
            name = f"{safe_name(number)}.json"
            _write_json_atomic(out / name, doc, indent=None)
            written.add(name)
            report["parsed"].append(
                {
                    "product_number": number,
                    "name": e["name"],
                    "file": name,
                    "smpc": [len(d["sections"]) for d in doc["smpc"]],
                    "pil": [len(d["sections"]) for d in doc["pil"]],
                    "warnings": doc["warnings"],
                }
            )
            log(f"[{i}/{len(todo)}] {number} {e['name']}: {len(doc['smpc'])} SmPC, {len(doc['pil'])} PIL")
    finally:
        if pool:
            pool.shutdown()
    if not only:
        for old in out.glob("EMEA-*.json"):
            if old.name not in written:
                old.unlink()
    report["summary"] = {
        "pdfs": len(todo),
        "parsed": len(report["parsed"]),
        "failed": len(report["failed"]),
        "json_bytes": sum((out / n).stat().st_size for n in written),
    }
    _write_json_atomic(out.with_name(out.name + "_report.json"), report)
    return report


# --- command line ------------------------------------------------------------------


def _page_list(spec: str) -> list[int]:
    pages: list[int] = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        pages.extend(range(int(a), int(b or a) + 1))
    return pages


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="EMA product information in Greek as structured JSON.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download", help="download the Greek PI PDFs, one request at a time")
    d.add_argument("--links", type=Path, default=Path("data/current/ema_links.csv"))
    d.add_argument("--cache", type=Path, default=Path("data/ema/pi"))
    d.add_argument("--limit", type=int, help="download at most N files in this run")
    d.add_argument("--refresh", action="store_true", help="download again files already on disk")
    p = sub.add_parser("parse", help="PDFs of the cache -> one JSON per product")
    p.add_argument("--cache", type=Path, default=Path("data/ema/pi"))
    p.add_argument("--out", type=Path, default=Path("data/current/ema_pi"))
    p.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    p.add_argument("--only", nargs="*", metavar="EMEA/H/C/NNNNNN", help="parse only these products")
    p.add_argument("--quiet", action="store_true", help="print failures and the summary only")
    ln = sub.add_parser("lines", help="print the text lines of a PDF in the fixture format")
    ln.add_argument("pdf", type=Path)
    ln.add_argument("--pages", help="e.g. 1-5,9")
    ln.add_argument("--raw", action="store_true", help="keep page numbers and running headers")
    args = ap.parse_args(argv)

    if args.cmd == "download":
        sources = read_sources(args.links)
        done, failures = download(Client(), sources, args.cache, args.limit, args.refresh)
        have = current_entries(args.cache)
        print(f"downloaded {done}; on disk {len(have)} of {len(sources)}; failed {len(failures)}")
        for number, why in failures:
            print(f"  FAILED {number}: {why}")
        return 1 if failures else 0
    if args.cmd == "parse":

        def log(msg: str) -> None:
            if not args.quiet or " FAILED " in msg:
                print(msg, flush=True)

        rep = parse_all(args.cache, args.out, args.jobs, args.only, log)
        s = rep["summary"]
        print(
            f"parsed {s['parsed']} of {s['pdfs']} PDFs ({s['failed']} failed); "
            f"JSON {s['json_bytes'] / 1e6:.1f} MB in {args.out}"
        )
        return 0
    if args.cmd == "lines":
        sys.stdout.reconfigure(encoding="utf-8")
        pages = read_pdf(args.pdf, _page_list(args.pages) if args.pages else None)
        if not args.raw:
            pages, _ = strip_furniture(pages)
        sys.stdout.write(lines_to_text(pages))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
