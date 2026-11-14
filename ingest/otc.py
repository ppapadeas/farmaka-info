"""Non-prescription medicines (ΜΗ.ΣΥ.ΦΑ.): read the Ministry's bulletins, build the current list.

Usage:
    python -m ingest.otc --manifest data/manifest_otc.json --raw data/raw --out data/current

Since law 4472/2017 (art. 86) and ministerial decision 38152/2017 (ΦΕΚ Β' 1761/2017)
a non-prescription medicine has an indicative retail price (not binding:
pharmacies may sell lower) and a maximum hospital price. The Ministry of Health
publishes them in their own listing (moh.OTC_BULLETINS), not in the price
bulletins. EOF consultation proposals are not final and are not used.

Merge rules (same idea as ingest/build.py; docs/DATA_SOURCES.md, S8):
  1. The newest complete list (kind in OTC_BASELINE_KINDS) read from xlsx is
     the baseline.
  2. The new-products bulletins (kind "new") published after it are applied in
     (published, article_id) order, upserting by eof_code.
  3. Amendments, and complete lists published only as pdf, are never applied
     automatically; they are listed in the report for review.
Every row keeps the bulletin it came from (published, moh_article).
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from dataclasses import dataclass, replace
from itertools import groupby
from pathlib import Path

import openpyxl

from .build import article_index
from .normalize import (
    AmbiguousValue,
    UnknownHeader,
    _code,
    _key,
    ean13_ok,
    parse_price,
    parse_vat,
    split_substances,
)

SCHEMA_VERSION = 1

# kinds of moh.classify_otc whose files ingest.fetch downloads
OTC_KINDS = ("repricing", "update", "new", "amendment")
# complete lists: the newest one is the baseline
OTC_BASELINE_KINDS = ("repricing", "update")

# canonical field -> header variants seen in the wild (compared via normalize._key,
# which ignores case, accents and punctuation: "Κωδικός Προϊόντος" == "ΚΩΔΙΚΟΣ ΠΡΟΪΟΝΤΟΣ")
_VARIANTS = {
    "eof_code": ["Κωδικός", "Κωδικός Προϊόντος", "Κωδικός Φαρμάκου"],
    "pack": ["ΣΥΣΚ."],  # 4821, 4834 (07/2017): 7-digit product code + 2-digit pack
    "barcode": ["BARCODE"],
    "name": ["Περιγραφή Προϊόντος", "ΟΝΟΜΑΣΙΑ ΠΡΟΪΟΝΤΟΣ", "Ονομασία Μορφή Περιεκτικότητα"],
    "atc": ["ATC", "ATCCODE"],
    # The final decisions often keep the header of the proposal ("Προτεινόμενη…").
    # 14636 (2026) calls it just "Λιανική Τιμή".
    "price_retail_indicative": [
        "Ενδεικτική Λιανική Τιμή",  # also "… (€)"
        "Προτεινόμενη Ενδεικτική Λιανική Τιμή",
        "Προτειν/νη Ενδεικτική Λιανική Τιμή",
        "Προτειν/νη Ενδεικτική Λ Τ",
        "Λιανική Τιμή",
    ],
    "price_hospital_max": [],  # no bulletin so far has this column
    "price_exfactory": ["Τιμή Παραγωγού"],  # 13661, 13685, 13738 (2025)
    "substance": [
        "Δραστική/ες",
        "Δραστική ουσία",
        "Δραστική/ές Ουσία/ες",
        "ΔΡΑΣΤΙΚΗ(ΕΣ)",
        "ΔΡΑΣΤΙΚΗ(ΕΣ) ΟΥΣΙΑ(ΕΣ)",
    ],
    "mah": ["ΟΝΟΜΑΣΙΑ ΚΑΚ"],  # and "ΚΑΚ", see _AMBIGUOUS
    "vat": ["ΦΠΑ"],
    # Recognised so they are not unknown headers, but not used.
    "note_otc": ["ΜΗ.ΣΥ.ΦΑ.", "ΜΗΣΥΦΑ"],  # 'N' on every row
    "note_new": ["Νέο Προϊόν"],  # 7298, 7829, 8484, 10266: marks new products
    "note_legal_basis": ["Νομική Βάση"],  # 4879, 5800: '10(3) - Αίτηση για Υβριδικό'
    "note_product_type": ["Τύπος Προϊόντος"],  # 4879: 'ΦΑΡΜΑΚΕΥΤΙΚΟ ΠΡΟΪΟΝ'
    "note_dispensing": ["Τρόπος Διάθεσης"],  # 5800
}
# Headers whose meaning depends on the other columns of the sheet: the first
# field not already taken by an unambiguous header wins.
_AMBIGUOUS = {
    # 13173: the product name; 5800: the product type, next to "ΠΕΡΙΓΡΑΦΗ ΠΡΟΪΟΝΤΟΣ"
    "ΠΡΟΪΟΝ": ("name", "note_product_type"),
    # mostly the holder's name; 4821, 4834: its code ('0000000003'), next to "ΟΝΟΜΑΣΙΑ ΚΑΚ"
    "ΚΑΚ": ("mah", "note_mah_code"),
    "KAK": ("mah", "note_mah_code"),  # Latin letters
}
HEADER_MAP = {_key(v): canon for canon, vs in _VARIANTS.items() for v in vs}
AMBIGUOUS = {_key(h): fields for h, fields in _AMBIGUOUS.items()}
REQUIRED = {"eof_code", "barcode", "name", "price_retail_indicative"}
_NULL_TEXT = {"(null)", "null"}  # 6740, 8060, 9189: ATC '(null)'


@dataclass
class OtcRow:
    eof_code: str
    barcode: str
    name: str
    atc: str | None
    substances: list[str]
    mah: str
    price_retail_indicative: float
    price_hospital_max: float | None
    price_exfactory: float | None
    vat: float | None
    published: str = ""
    moh_article: int | None = None


def _text(v) -> str:
    return " ".join(str(v).split()) if v is not None else ""


def _opt_price(v) -> float | None:
    return None if v is None or str(v).strip() == "" else parse_price(v)


def _opt_vat(v) -> float | None:
    return None if v is None or str(v).strip() == "" else parse_vat(v)


_TEXT_MARKS = "'΄’`"  # typed in front of a number to keep it text: '΄022830102' (7829)


def digits_of(raw) -> tuple[str, bool]:
    """A code or barcode cell as text, and whether a stray text mark was removed."""
    s = _code(raw) if raw is not None else ""
    t = s.lstrip(_TEXT_MARKS)
    return (t, True) if t != s and t.isdigit() else (s, False)


def eof_code_of(raw, barcode: str) -> tuple[str, str | None]:
    """The 9-digit EOF code of a row, and a note if it had to be repaired.

    Codes come as text ('023280803', '338280101\\n') or as numbers that lost
    their leading zeros (23280605 for 023280605, moh article 14636). A short
    numeric code is padded only when the barcode embeds the padded code; an
    empty code is taken from a valid barcode; a stray mark before the digits
    ('΄022830102', 7829) is dropped. Anything else is returned as is, for the
    caller to reject."""
    code, marked = digits_of(raw)
    bc_ok = ean13_ok(barcode) and barcode.startswith("280")
    if not code and bc_ok:
        return barcode[3:12], "empty eof_code; taken from the barcode"
    if code.isdigit() and len(code) < 9 and bc_ok and barcode[3:12] == code.zfill(9):
        return code.zfill(9), "eof_code lost its leading zeros; restored from the barcode"
    if marked:
        return code, "stray mark before the eof_code removed"
    return code, None


def _header(path: Path, ws) -> tuple[dict[str, int], object]:
    """Column of every canonical field, and the rows after the header.

    The header is the first row with a recognised code or barcode column. Rows
    above it may only be titles (one cell, e.g. 'ΠΙΝΑΚΑΣ' in 5412, 5720)."""
    rows = ws.iter_rows(values_only=True)
    for header in rows:
        cells = [(i, h) for i, h in enumerate(header or ()) if h is not None and str(h).strip() != ""]
        if any(HEADER_MAP.get(_key(h)) in ("eof_code", "barcode") for _, h in cells):
            break
        if len(cells) > 1:
            raise UnknownHeader(f"{path.name} [{ws.title}]: unknown header row {header!r}")
    else:
        return {}, rows
    cols: dict[str, int] = {}
    later = []
    for i, h in cells:
        k = _key(h)
        if k in AMBIGUOUS:
            later.append((i, h))
            continue
        canon = HEADER_MAP.get(k)
        if canon is None:
            raise UnknownHeader(f"{path.name} [{ws.title}]: unknown column {h!r}")
        if canon in cols:
            raise UnknownHeader(f"{path.name} [{ws.title}]: two columns for {canon}: {h!r}")
        cols[canon] = i
    for i, h in later:
        canon = next((f for f in AMBIGUOUS[_key(h)] if f not in cols), None)
        if canon is None:
            raise UnknownHeader(f"{path.name} [{ws.title}]: no free field for column {h!r}")
        cols[canon] = i
    missing = REQUIRED - cols.keys()
    if missing:
        raise UnknownHeader(f"{path.name} [{ws.title}]: missing required columns {sorted(missing)}")
    return cols, rows


def read_otc(path: Path, issues: list[dict] | None = None, fixes: list[dict] | None = None) -> list[OtcRow]:
    """Parse one ΜΗ.ΣΥ.ΦΑ. bulletin (xlsx; every sheet with data).

    Rows with an unreadable price or code are skipped and appended to `issues`
    for manual review (with issues=None they raise). Repaired codes are
    appended to `fixes`."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out: list[OtcRow] = []
    for ws in wb.worksheets:
        cols, rows = _header(path, ws)
        if not cols:
            continue  # empty sheet
        for r in rows:
            if r is None or all(c is None or str(c).strip() == "" for c in r):
                continue

            def g(k: str, r=r):
                return r[cols[k]] if k in cols and cols[k] < len(r) else None

            barcode, marked = digits_of(g("barcode"))
            raw_code = g("eof_code")
            if "pack" in cols and raw_code is not None:  # 2017: product code + pack
                raw_code = _code(raw_code) + _code(g("pack") or "")
            code, note = eof_code_of(raw_code, barcode)
            name = _text(g("name"))
            problem = None
            if not (len(code) == 9 and code.isdigit()):
                problem = f"eof_code {code!r} is not 9 digits"
            else:
                try:
                    price = parse_price(g("price_retail_indicative"))
                    hospital = _opt_price(g("price_hospital_max"))
                    exfactory = _opt_price(g("price_exfactory"))
                except AmbiguousValue as e:
                    problem = str(e)
            if problem:
                if issues is None:
                    raise AmbiguousValue(f"{path.name}: {problem}: {r!r}")
                issues.append({"file": path.name, "eof_code": code, "name": name, "problem": problem})
                continue
            if fixes is not None:
                if note:
                    fixes.append({"file": path.name, "eof_code": code, "name": name, "fix": note})
                if marked:
                    fix = "stray mark before the barcode removed"
                    fixes.append({"file": path.name, "eof_code": code, "name": name, "fix": fix})
            out.append(
                OtcRow(
                    eof_code=code,
                    barcode=barcode,
                    name=name,
                    atc=None if _text(g("atc")).lower() in _NULL_TEXT else _text(g("atc")) or None,
                    substances=split_substances(_text(g("substance"))),
                    mah=_text(g("mah")),
                    price_retail_indicative=price,
                    price_hospital_max=hospital,
                    price_exfactory=exfactory,
                    vat=_opt_vat(g("vat")),
                )
            )
    return out


def validate(rows: dict[str, OtcRow]) -> list[str]:
    problems = []
    for code, r in sorted(rows.items()):
        if not ean13_ok(r.barcode):
            problems.append(f"{code}: invalid EAN-13 {r.barcode!r}")
        elif r.barcode[3:12] != code:
            problems.append(f"{code}: barcode {r.barcode} does not embed eof_code")
        if not r.atc:
            problems.append(f"{code}: missing ATC")
        if not r.substances:
            problems.append(f"{code}: missing substance")
        if not r.price_retail_indicative > 0:
            problems.append(f"{code}: indicative retail price {r.price_retail_indicative} not positive")
        if r.price_hospital_max is not None and r.price_hospital_max > r.price_retail_indicative:
            problems.append(f"{code}: maximum hospital price above the indicative retail price")
    return problems


def _order(s) -> tuple[str, int]:
    return (s[0], s[1])


_FIELDS_TO_FILL = ("atc", "mah", "price_hospital_max", "price_exfactory", "vat")


def _fill(r: OtcRow, old: OtcRow) -> OtcRow:
    """`r` with the fields it leaves empty taken from `old`."""
    for f in _FIELDS_TO_FILL:
        if getattr(r, f) in (None, ""):
            r = replace(r, **{f: getattr(old, f)})
    return r if r.substances else replace(r, substances=old.substances)


def _read_article(paths, issues, fixes) -> dict[str, OtcRow]:
    """All rows of one article (several files or sheets) by eof_code.

    The same code may be listed twice (7298, 8484, 10266: the complete list and
    the new products); empty fields are filled from the other listing. Two
    different prices for one code are an error, or a review item with `issues`
    (and neither is applied)."""
    listed: dict[str, OtcRow] = {}
    held: set[str] = set()
    for path in paths:
        for r in read_otc(path, issues, fixes):
            if r.eof_code in held:
                continue
            old = listed.get(r.eof_code)
            if old is not None and old.price_retail_indicative != r.price_retail_indicative:
                problem = f"listed twice in one article: {old.price_retail_indicative} and {r.price_retail_indicative}"
                if issues is None:
                    raise AmbiguousValue(f"{path.name}: {r.eof_code} {problem}")
                issues.append({"file": path.name, "eof_code": r.eof_code, "name": r.name, "problem": problem})
                del listed[r.eof_code]
                held.add(r.eof_code)
                continue
            listed[r.eof_code] = _fill(r, old) if old is not None else r
    return listed


def merge(
    sources: list[tuple[str, int, str, Path]],
    issues: list[dict] | None = None,
    fixes: list[dict] | None = None,
    report: dict | None = None,
) -> tuple[tuple[str, int, str], dict[str, OtcRow]]:
    """Newest complete list + the new-products bulletins after it.

    `sources` are (published, article_id, kind, path); an article may have
    several files. Returns the baseline (published, article_id, kind) and the
    current rows by eof_code, each with the bulletin that set it. If `report`
    is given, it gets "applied" (articles in the order applied, with their row
    counts), "relisted" (codes a new-products bulletin lists again, with the
    old and new price) and "dropped" (codes the previous complete list, or a
    new-products bulletin between the two, had and the baseline lacks)."""
    articles = [
        (key, [s[3] for s in group])
        for key, group in groupby(sorted(sources, key=lambda s: (_order(s), str(s[3]))), key=lambda s: s[:3])
    ]
    complete = [a for a in articles if a[0][2] in OTC_BASELINE_KINDS]
    if not complete:
        raise SystemExit("no complete ΜΗ.ΣΥ.ΦΑ. list (kind repricing/update) found")
    base_key, base_paths = complete[-1]
    current: dict[str, OtcRow] = {}
    for code, r in _read_article(base_paths, issues, fixes).items():
        current[code] = replace(r, published=base_key[0], moh_article=base_key[1])
    applied = [{"key": base_key, "files": base_paths, "rows": len(current), "role": "baseline"}]
    relisted: list[dict] = []
    for key, paths in articles:
        if key[2] != "new" or _order(key) <= _order(base_key):
            continue
        listed = _read_article(paths, issues, fixes)
        for code, r in listed.items():
            old = current.get(code)
            r = replace(r, published=key[0], moh_article=key[1])
            if old is not None:
                r = _fill(r, old)  # keep earlier values for fields this bulletin leaves empty
                relisted.append(
                    {
                        "eof_code": code,
                        "name": r.name,
                        "old": {
                            "price_retail_indicative": old.price_retail_indicative,
                            "moh_article": old.moh_article,
                        },
                        "new": {"price_retail_indicative": r.price_retail_indicative, "moh_article": key[1]},
                    }
                )
            current[code] = r
        applied.append({"key": key, "files": paths, "rows": len(listed), "role": "new"})
    if report is not None:
        report["applied"] = applied
        report["relisted"] = relisted
        report["dropped"] = []
        if len(complete) > 1:
            prev_key, prev_paths = complete[-2]
            before: dict[str, tuple[OtcRow, int]] = {
                c: (r, prev_key[1]) for c, r in _read_article(prev_paths, [], []).items()
            }
            for key, paths in articles:
                if key[2] == "new" and _order(prev_key) < _order(key) < _order(base_key):
                    before.update({c: (r, key[1]) for c, r in _read_article(paths, [], []).items()})
            report["dropped"] = [
                {"eof_code": c, "name": r.name, "last_listed_in": aid}
                for c, (r, aid) in sorted(before.items())
                if c not in current
            ]
            report["previous_complete_list"] = {"key": prev_key, "files": prev_paths}
    return base_key, current


OTC_FIELDS = [
    "eof_code",
    "barcode",
    "name",
    "atc",
    "substances",
    "mah",
    "price_retail_indicative",
    "price_hospital_max",
    "vat",
    "published",
    "moh_article",
]


def _blank(v):
    return "" if v is None else v


def write_csv(path: Path, rows: dict[str, OtcRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(OTC_FIELDS)
        for code in sorted(rows):
            r = rows[code]
            w.writerow(
                [
                    r.eof_code,
                    r.barcode,
                    r.name,
                    r.atc or "",
                    "|".join(r.substances),
                    r.mah,
                    r.price_retail_indicative,
                    _blank(r.price_hospital_max),
                    _blank(r.vat),
                    r.published,
                    _blank(r.moh_article),
                ]
            )


def overlap(rows: dict[str, OtcRow], packages_csv: Path) -> dict:
    """Codes that are also in the regulated price list (packages.csv). Reported only."""
    if not packages_csv.exists():
        return {"file": str(packages_csv), "available": False, "count": 0, "packages": []}
    with packages_csv.open(encoding="utf-8", newline="") as fh:
        prices = {p["eof_code"]: p for p in csv.DictReader(fh)}
    both = []
    for code in sorted(rows.keys() & prices.keys()):
        r, p = rows[code], prices[code]
        both.append(
            {
                "eof_code": code,
                "otc_name": r.name,
                "otc_price_retail_indicative": r.price_retail_indicative,
                "otc_moh_article": r.moh_article,
                "price_list_name": p["name"],
                "price_list_price_retail": float(p["price_retail"]),
                "price_list_moh_article": int(p["price_moh_article"]) if p["price_moh_article"] else None,
            }
        )
    return {"file": packages_csv.name, "available": True, "count": len(both), "packages": both}


def pending_bulletins(articles: dict[int, dict], applied_ids: set[int], base_key) -> list[dict]:
    """Bulletins of the listing published after the baseline and not applied:
    amendments, complete lists without xlsx, files not downloaded yet."""
    out = []
    for aid, a in sorted(articles.items(), key=lambda kv: (kv[1]["published"], kv[0])):
        if a["kind"] not in OTC_KINDS or aid in applied_ids:
            continue
        if (a["published"], aid) <= _order(base_key):
            continue
        if a["kind"] == "amendment":
            reason = "needs manual review"
        elif a.get("checked") and not a.get("has_xlsx"):
            reason = "no xlsx (only pdf); not read"
        else:
            reason = "not downloaded yet"
        out.append(
            {k: a[k] for k in ("moh_article", "published", "kind", "title", "url")} | {"reason": reason}
        )
    return out


def sources_from_manifest(manifest: list[dict], raw: Path) -> list[tuple[str, int, str, Path]]:
    out = []
    for art in manifest:
        if art["kind"] not in OTC_BASELINE_KINDS + ("new",):
            continue
        d, m, y = art["date"].split("/")
        for att in art["attachments"]:
            p = raw / f"moh_{art['article_id']}_{att['fdl']}.xlsx"
            if p.exists():
                out.append((f"{y}-{m}-{d}", art["article_id"], art["kind"], p))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--raw", type=Path, default=Path("data/raw"))
    ap.add_argument("--out", type=Path, default=Path("data/current"))
    ap.add_argument("--packages", type=Path, help="price list to compare with (default: OUT/packages.csv)")
    a = ap.parse_args()

    manifest = json.loads(a.manifest.read_text(encoding="utf-8"))
    articles = article_index(manifest)
    for art in manifest:
        articles[art["article_id"]]["checked"] = art.get("checked", False)
        articles[art["article_id"]]["has_xlsx"] = any(
            x["label"].split(" Μέγεθος:")[0].strip().lower().endswith(".xlsx") for x in art["attachments"]
        )
    sources = sources_from_manifest(manifest, a.raw)
    issues: list[dict] = []
    fixes: list[dict] = []
    report: dict = {}
    base_key, rows = merge(sources, issues, fixes, report)
    problems = validate(rows)
    applied_ids = {x["key"][1] for x in report["applied"]}
    pending = pending_bulletins(articles, applied_ids, base_key)
    both = overlap(rows, a.packages or a.out / "packages.csv")

    write_csv(a.out / "otc_packages.csv", rows)

    def meta(key, files=(), **extra):
        art = articles.get(key[1], {})
        return {
            "moh_article": key[1],
            "published": key[0],
            "kind": key[2],
            "title": art.get("title", ""),
            "url": art.get("url", ""),
            "files": [p.name for p in files],
        } | extra

    by_source = Counter(r.moh_article for r in rows.values())
    prev = report.get("previous_complete_list")
    out = {
        "schema_version": SCHEMA_VERSION,
        "note": "Indicative retail prices (not binding); not part of the regulated price list.",
        "baseline": meta(report["applied"][0]["key"], report["applied"][0]["files"]),
        "sources": [
            meta(
                x["key"], x["files"], role=x["role"], rows_read=x["rows"], rows_current=by_source[x["key"][1]]
            )
            for x in report["applied"]
        ],
        "counts": {
            "packages": len(rows),
            "from_baseline": by_source[base_key[1]],
            "from_new_bulletins": len(rows) - by_source[base_key[1]],
            "relisted_by_new_bulletins": len(report["relisted"]),
            "with_hospital_price": sum(r.price_hospital_max is not None for r in rows.values()),
            "without_vat": sum(r.vat is None for r in rows.values()),
            "codes_repaired": len(fixes),
            "review_queue": len(issues),
            "validation_problems": len(problems),
            "pending_bulletins": len(pending),
            "dropped_by_baseline": len(report["dropped"]),
            "also_in_price_list": both["count"],
        },
        "validation_problems": problems,
        "review_queue": issues,
        "codes_repaired": fixes,
        "pending_bulletins": pending,
        "relisted": report["relisted"],
        "dropped_by_baseline": {
            "previous_complete_list": meta(prev["key"], prev["files"]) if prev else None,
            "packages": report["dropped"],
        },
        "also_in_price_list": both,
    }
    (a.out / "otc_report.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"baseline {base_key[1]} ({base_key[0]}, {base_key[2]}); "
        f"{len(report['applied']) - 1} new-products bulletins; {len(rows)} packages; "
        f"{len(problems)} validation problems; {len(issues)} rows for manual review; "
        f"{len(pending)} bulletins pending; {both['count']} also in the price list -> {a.out}"
    )


if __name__ == "__main__":
    main()
