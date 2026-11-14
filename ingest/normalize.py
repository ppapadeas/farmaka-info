"""Read MoH price bulletin spreadsheets into one canonical row format.

Every bulletin uses slightly different headers for the same columns. HEADER_MAP
folds them onto canonical field names; unknown headers raise, so a new variant
shows up as a loud failure instead of silently dropped data.
"""

from __future__ import annotations

import re
import unicodedata
import warnings
from dataclasses import dataclass
from pathlib import Path

import openpyxl

# MoH spreadsheets carry print headers openpyxl cannot parse; harmless.
warnings.filterwarnings("ignore", message="Cannot parse header or footer", module="openpyxl")


def _key(h: str) -> str:
    """Case-, accent- and punctuation-insensitive header key."""
    h = "".join(c for c in unicodedata.normalize("NFD", str(h)) if unicodedata.category(c) != "Mn")
    return re.sub(r"[^0-9a-zα-ω]+", " ", h.lower()).strip()


# canonical field -> header variants seen in the wild (compared via _key)
_VARIANTS = {
    "eof_code": ["Κωδικός", "ΚΩΔΙΚΟΣ"],
    "barcode": ["Barcode", "BARCODE"],
    "name": ["Προϊόν", "Περιγραφή Προϊόντος", "ΟΝΟΜΑΣΙΑ - ΜΟΡΦΗ - ΠΕΡΙΕΚΤΙΚΟΤΗΤΑ"],
    "atc": ["ATC"],
    "non_reimbursed": ["Μη Αποζημιούμενο", "ΜΗ ΑΠΟΖΗΜΙΟΥΜΕΝΟ"],
    "price_exfactory": ["Τιμή Παραγωγού", "ΤΙΜΗ ΠΑΡΑΓΩΓΟΥ"],
    "price_wholesale": ["Χονδρική Τιμή", "ΧΟΝΔΡΙΚΗ ΤΙΜΗ"],
    "price_retail": ["Λιανική Τιμή", "ΛΙΑΝΙΚΗ ΤΙΜΗ"],
    "substance": ["Δραστική ουσία", "Δραστική/ες", "Δραστική(ες)", "Δραστική(ές) Ουσία(ες)"],
    "mah": ["Κάτοχος Άδειας Κυκλοφορίας", "KAK", "ΚΑΚ", "ΟΝΟΝΑΣΙΑ ΚΑΚ", "ΟΝΟΜΑΣΙΑ ΚΑΚ"],
    "vat": ["ΦΠΑ"],
}
HEADER_MAP = {_key(v): canon for canon, vs in _VARIANTS.items() for v in vs}

FIELDS = list(_VARIANTS)


class UnknownHeader(ValueError):
    pass


@dataclass
class PriceRow:
    eof_code: str
    barcode: str
    name: str
    atc: str | None
    non_reimbursed: bool
    price_exfactory: float
    price_wholesale: float
    price_retail: float
    substances: list[str]
    mah: str
    vat: float


_SUBST_SPLIT = re.compile(r"\s*[:,+]\s*")


def split_substances(raw: str | None) -> list[str]:
    """Combination products use ':' (most), ',' or '+' between substances.

    '/' is NOT split: it also appears inside single substance names.
    """
    if not raw:
        return []
    return [s.strip() for s in _SUBST_SPLIT.split(str(raw)) if s.strip()]


def _flag(v) -> bool:
    # Bulletins mix Latin 'N' and Greek 'Ν'; blank / None means reimbursed.
    return str(v or "").strip().upper() in {"N", "Ν"}


def _code(v) -> str:
    s = str(v).strip()
    s = s.removesuffix(".0")
    return s


class AmbiguousValue(ValueError):
    pass


_NUM_RE = re.compile(r"\d{1,3}(?:\.\d{3})*,\d+|\d+(?:[.,]\d+)?")


def parse_price(v) -> float:
    """Numbers pass through. Text cells (seen in real bulletins) may use a Greek
    decimal comma and stray newlines: '\\n\\n38,93' -> 38.93.

    Cells holding more than one number (e.g. '17,07\\n\\n19,40') are ambiguous
    (old/new? two packs?) and raise AmbiguousValue for manual review.
    """
    if isinstance(v, (int, float)):
        return float(v)
    if v is None:
        raise AmbiguousValue("empty price")
    nums = _NUM_RE.findall(str(v))
    if len(nums) != 1:
        raise AmbiguousValue(f"price cell {str(v)!r}")
    n = nums[0]
    if "," in n:
        n = n.replace(".", "").replace(",", ".")
    return float(n)


def parse_vat(v) -> float:
    """VAT appears as 0.06, 6 or '6%' depending on the bulletin."""
    if v is None or str(v).strip() == "":
        return 0.0
    if isinstance(v, (int, float)):
        x = float(v)
    else:
        x = float(str(v).strip().rstrip("%").replace(",", "."))
    return x / 100 if x >= 1 else x


def ean13_ok(b: str) -> bool:
    if not re.fullmatch(r"\d{13}", b):
        return False
    d = [int(x) for x in b]
    return (10 - sum(d[i] * (3 if i % 2 else 1) for i in range(12)) % 10) % 10 == d[12]


def read_bulletin(path: Path, issues: list[dict] | None = None) -> list[PriceRow]:
    """Parse one bulletin. Rows with ambiguous values are skipped and appended to
    `issues` (if given) for manual review; with issues=None they raise."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    rows = ws.iter_rows(values_only=True)
    header = next(rows)
    cols: dict[str, int] = {}
    for i, h in enumerate(header):
        if h is None or str(h).strip() == "":
            continue
        canon = HEADER_MAP.get(_key(h))
        if canon is None:
            raise UnknownHeader(f"{path.name}: unknown column {h!r}")
        cols[canon] = i
    missing = {"eof_code", "barcode", "name", "price_retail"} - cols.keys()
    if missing:
        raise UnknownHeader(f"{path.name}: missing required columns {sorted(missing)}")

    out: list[PriceRow] = []
    for r in rows:
        if r is None or all(c is None for c in r):
            continue

        def g(k: str, r=r):
            return r[cols[k]] if k in cols and cols[k] < len(r) else None

        if g("eof_code") is None:
            continue
        atc = (str(g("atc")).strip() or None) if g("atc") is not None else None
        try:
            prices = [parse_price(g(k)) for k in ("price_exfactory", "price_wholesale", "price_retail")]
        except AmbiguousValue as e:
            if issues is None:
                raise
            issues.append(
                {
                    "file": path.name,
                    "eof_code": _code(g("eof_code")),
                    "name": str(g("name")).strip(),
                    "problem": str(e),
                }
            )
            continue
        out.append(
            PriceRow(
                eof_code=_code(g("eof_code")),
                barcode=_code(g("barcode")),
                name=str(g("name")).strip(),
                atc=atc,
                non_reimbursed=_flag(g("non_reimbursed")),
                price_exfactory=prices[0],
                price_wholesale=prices[1],
                price_retail=prices[2],
                substances=split_substances(g("substance")),
                mah=str(g("mah") or "").strip(),
                vat=parse_vat(g("vat")),
            )
        )
    return out
