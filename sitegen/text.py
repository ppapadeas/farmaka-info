"""Text helpers for the site: search skeleton, slugs, Greek number/date format.

`skeleton` must stay identical to web/static/search-core.js::skeleton; both are
checked against tests/skeleton_vectors.json.
"""

from __future__ import annotations

import re
import unicodedata

_GREEK_DIGRAPHS = [
    ("ου", "u"),
    ("αι", "e"),
    ("ει", "i"),
    ("οι", "i"),
    ("υι", "i"),
    ("αυ", "av"),
    ("ευ", "ev"),
    ("μπ", "mp"),
    ("ντ", "nt"),
    ("γκ", "g"),
    ("γγ", "g"),
    ("τσ", "ts"),
    ("τζ", "tz"),
]
_GREEK_LETTERS = "αβγδεζηθικλμνξοπρσςτυφχψω"
_GREEK = dict(
    zip(
        _GREEK_LETTERS,
        ["a", "v", "g", "d", "e", "z", "i", "th", "i", "k", "l", "m", "n"]
        + ["ks", "o", "p", "r", "s", "s", "t", "i", "f", "ch", "ps", "o"],
        strict=True,
    )
)
_LATIN = [
    ("ph", "f"),
    ("h", ""),
    ("c", "k"),
    ("q", "k"),
    ("x", "ks"),
    ("w", "v"),
    ("j", "i"),
    ("y", "i"),
    ("mp", "v"),
    ("b", "v"),
    ("nt", "d"),
    ("ou", "u"),
    ("e", "i"),
]
_MARKS = re.compile("[̀-ͯ]")
_GREEK_LETTER = re.compile("[α-ω]")


def skeleton(text: str | None) -> str:
    s = _MARKS.sub("", unicodedata.normalize("NFD", str(text or "").lower()))
    for a, b in _GREEK_DIGRAPHS:
        s = s.replace(a, b)
    s = _GREEK_LETTER.sub(lambda m: _GREEK.get(m.group(), ""), s)
    for a, b in _LATIN:
        s = s.replace(a, b)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"([a-z])\1+", r"\1", s)
    return s.strip()


# Plain transliteration for URLs (not phonetic): ΜΕΤΦΟΡΜΙΝΗ -> metforminh is ugly,
# so use the ELOT-like single letters.
_SLUG_GREEK = dict(
    zip(
        _GREEK_LETTERS,
        ["a", "v", "g", "d", "e", "z", "i", "th", "i", "k", "l", "m", "n"]
        + ["x", "o", "p", "r", "s", "s", "t", "y", "f", "ch", "ps", "o"],
        strict=True,
    )
)


def slugify(text: str) -> str:
    s = _MARKS.sub("", unicodedata.normalize("NFD", text.lower()))
    s = _GREEK_LETTER.sub(lambda m: _SLUG_GREEK.get(m.group(), ""), s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "x"


def eur(x: float | None) -> str:
    """1234.5 -> '1.234,50 €'."""
    if x is None:
        return "—"
    s = f"{x:,.2f}".replace(",", "\0").replace(".", ",").replace("\0", ".")
    return f"{s} €"


def num(x: float | None, digits: int = 2) -> str:
    if x is None:
        return "—"
    s = f"{x:,.{digits}f}".replace(",", "\0").replace(".", ",").replace("\0", ".")
    return s


def date_gr(iso: str | None) -> str:
    """'2026-10-05' -> '05/10/2026'."""
    if not iso:
        return "—"
    y, m, d = iso[:10].split("-")
    return f"{d}/{m}/{y}"
