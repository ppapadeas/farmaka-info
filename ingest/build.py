"""Build the current price list from one baseline plus the deltas after it.

Usage:
    python -m ingest.build --manifest data/manifest.json --raw data/raw --out data/current
    python -m ingest.build --samples          # offline run on samples/raw

Merge rules (see docs/DATA_SOURCES.md, "Merge semantics"):
  1. The newest bulletin of kind "full" is the baseline.
  2. Every "delta" published after the baseline is applied in (date, article_id)
     order; a row upserts by eof_code.
  3. Every applied change is recorded in price_history.csv with its source.
Known gaps: withdrawals are not visible in deltas; effective dates may differ
from publication dates. Both are open questions in docs/DECISIONS.md.
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict
from pathlib import Path

from .normalize import PriceRow, ean13_ok, read_bulletin

ROOT = Path(__file__).resolve().parent.parent

# Offline sample set: (iso_date, article_id, kind, file)
SAMPLES = [
    ("2026-10-05", 14818, "full", "moh_14818_epikairopoihsh_2026-09.xlsx"),
    ("2026-10-21", 14872, "delta", "moh_14872_genosima_2026-08.xlsx"),
    ("2026-11-06", 14930, "delta", "moh_14930_symplhrwmatiko_xydalba.xlsx"),
]

PRICE_FIELDS = ("price_exfactory", "price_wholesale", "price_retail", "non_reimbursed")


def merge(sources: list[tuple[str, int, str, Path]], issues: list[dict] | None = None):
    fulls = [s for s in sources if s[2] == "full"]
    if not fulls:
        raise SystemExit("no baseline (kind=full) bulletin found")
    base = max(fulls, key=lambda s: (s[0], s[1]))
    current: dict[str, PriceRow] = {}
    history: list[dict] = []
    for r in read_bulletin(base[3], issues):
        current[r.eof_code] = r
    deltas = sorted(
        (s for s in sources if s[2] == "delta" and (s[0], s[1]) > (base[0], base[1])),
        key=lambda s: (s[0], s[1]),
    )
    for date, aid, _, path in deltas:
        for r in read_bulletin(path, issues):
            old = current.get(r.eof_code)
            change = "new" if old is None else "update"
            for f in PRICE_FIELDS:
                ov = getattr(old, f) if old else None
                nv = getattr(r, f)
                if ov != nv:
                    history.append(
                        {
                            "eof_code": r.eof_code,
                            "field": f,
                            "old": ov,
                            "new": nv,
                            "change": change,
                            "published": date,
                            "moh_article": aid,
                        }
                    )
            if old is not None:
                # keep baseline values for fields a delta leaves empty
                for f in ("atc", "mah"):
                    if not getattr(r, f):
                        setattr(r, f, getattr(old, f))
                if not r.substances:
                    r.substances = old.substances
            current[r.eof_code] = r
    return base, deltas, current, history


def validate(rows: dict[str, PriceRow]) -> list[str]:
    problems = []
    for code, r in rows.items():
        if len(code) != 9 or not code.isdigit():
            problems.append(f"{code}: eof_code not 9 digits")
        if not ean13_ok(r.barcode):
            problems.append(f"{code}: invalid EAN-13 {r.barcode}")
        elif r.barcode[3:12] != code:
            problems.append(f"{code}: barcode does not embed eof_code")
        if not r.atc:
            problems.append(f"{code}: missing ATC")
        elif len(r.atc) != 7:
            problems.append(f"{code}: ATC not at substance level ({r.atc})")
        if not r.substances:
            problems.append(f"{code}: missing substance")
        if not (0 < r.price_exfactory <= r.price_wholesale <= r.price_retail):
            problems.append(f"{code}: price ordering exfactory<=wholesale<=retail violated")
    return problems


def write(out: Path, base, deltas, rows: dict[str, PriceRow], history, problems, issues=()) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with (out / "packages.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "eof_code",
                "barcode",
                "name",
                "atc",
                "substances",
                "mah",
                "non_reimbursed",
                "price_exfactory",
                "price_wholesale",
                "price_retail",
                "vat",
            ]
        )
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
                    int(r.non_reimbursed),
                    r.price_exfactory,
                    r.price_wholesale,
                    r.price_retail,
                    r.vat,
                ]
            )
    with (out / "price_history.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(
            fh, fieldnames=["eof_code", "field", "old", "new", "change", "published", "moh_article"]
        )
        w.writeheader()
        w.writerows(history)
    (out / "packages.json").write_text(
        json.dumps([asdict(rows[c]) for c in sorted(rows)], ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (out / "build_report.json").write_text(
        json.dumps(
            {
                "baseline": {"published": base[0], "moh_article": base[1], "file": base[3].name},
                "deltas_applied": [
                    {"published": d[0], "moh_article": d[1], "file": d[3].name} for d in deltas
                ],
                "packages": len(rows),
                "history_events": len(history),
                "validation_problems": problems,
                "review_queue": list(issues),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", action="store_true", help="offline run on samples/raw")
    ap.add_argument("--manifest", type=Path)
    ap.add_argument("--raw", type=Path, default=ROOT / "data/raw")
    ap.add_argument("--out", type=Path, default=ROOT / "data/current")
    a = ap.parse_args()

    if a.samples:
        sources = [(d, i, k, ROOT / "samples/raw" / f) for d, i, k, f in SAMPLES]
    else:
        if not a.manifest:
            ap.error("--manifest or --samples required")
        sources = []
        for art in json.loads(a.manifest.read_text(encoding="utf-8")):
            if art["kind"] not in ("full", "delta"):
                continue
            d, m, y = art["date"].split("/")
            for att in art["attachments"]:
                p = a.raw / f"moh_{art['article_id']}_{att['fdl']}.xlsx"
                if p.exists():
                    sources.append((f"{y}-{m}-{d}", art["article_id"], art["kind"], p))

    issues: list[dict] = []
    base, deltas, rows, history = merge(sources, issues)
    problems = validate(rows)
    write(a.out, base, deltas, rows, history, problems, issues)
    print(
        f"baseline {base[3].name}; {len(deltas)} deltas; {len(rows)} packages; "
        f"{len(history)} changes; {len(problems)} validation problems; "
        f"{len(issues)} rows for manual review -> {a.out}"
    )


if __name__ == "__main__":
    main()
