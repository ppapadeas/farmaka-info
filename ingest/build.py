"""Build the current price list and the price history from MoH bulletins.

Usage:
    python -m ingest.build --manifest data/manifest.json --raw data/raw --out data/current
    python -m ingest.build --samples          # offline run on samples/raw

Merge rules (see docs/DATA_SOURCES.md, "Merge semantics"):
  1. Every bulletin of kind "full" (complete list) is applied in (published,
     article_id) order, with the "delta"s published between them in the same
     order. Deltas older than the oldest full are skipped (nothing to apply
     them to) and listed in the build report.
  2. A full replaces the state: a code it lacks is withdrawn, a withdrawn code
     it lists again reappears, a changed price is an update. A delta upserts
     by eof_code.
  3. So the current state is the newest full plus the deltas after it.
  4. Every change is recorded in price_history.csv with its source; see CHANGES.
Known gap: effective dates may differ from publication dates (docs/DECISIONS.md, A-5).
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from dataclasses import asdict
from itertools import groupby
from pathlib import Path

from .normalize import PriceRow, ean13_ok, read_bulletin

ROOT = Path(__file__).resolve().parent.parent

SCHEMA_VERSION = 1
SAMPLES_MANIFEST = ROOT / "samples/manifests/moh_prices_listing_2026-11-14.json"

# Offline sample set: (iso_date, article_id, kind, file)
SAMPLES = [
    ("2026-10-05", 14818, "full", "moh_14818_epikairopoihsh_2026-09.xlsx"),
    ("2026-10-21", 14872, "delta", "moh_14872_genosima_2026-08.xlsx"),
    ("2026-11-06", 14930, "delta", "moh_14930_symplhrwmatiko_xydalba.xlsx"),
]

PRICE_FIELDS = ("price_exfactory", "price_wholesale", "price_retail", "non_reimbursed")

# `change` values in price_history.csv (docs/SCHEMA.md, PriceEvent). There is one
# row per price field, and per (eof_code, field) `old` is the previous row's `new`;
# an empty value means "not on the list".
#   first_seen  the code is listed for the first time, by a full list (old empty)
#   new         the code is listed for the first time, by a delta (old empty)
#   update      a value changed, by a delta or between the state and the next full
#   withdrawn   a full list lacks a code the state had (new empty)
#   reappeared  a withdrawn code is listed again, by a full or a delta (old empty)
CHANGES = ("first_seen", "new", "update", "withdrawn", "reappeared")


def _order(s) -> tuple[str, int]:
    return (s[0], s[1])


def _events(code: str, change: str, old, new, published: str, article: int) -> list[dict]:
    """One event per price field that differs; `old`/`new` is None when not listed."""
    out = []
    for f in PRICE_FIELDS:
        ov = getattr(old, f) if old is not None else None
        nv = getattr(new, f) if new is not None else None
        if ov != nv:
            out.append(
                {
                    "eof_code": code,
                    "field": f,
                    "old": ov,
                    "new": nv,
                    "change": change,
                    "published": published,
                    "moh_article": article,
                }
            )
    return out


def merge(
    sources: list[tuple[str, int, str, Path]],
    issues: list[dict] | None = None,
    provenance: dict[str, dict] | None = None,
    report: dict | None = None,
):
    """Walk every full list in order, with the deltas between and after them.

    Returns (base, deltas, current, history): the newest full, the deltas after
    it, the current state (newest full + those deltas) and every event.
    If `provenance` is given, it is filled with the bulletin that set each
    package's current prices: {eof_code: {published, moh_article}}.
    If `report` is given, it gets "applied" (sources in the order applied),
    "skipped" (deltas older than the oldest full) and "withdrawn" (codes the
    newest full dropped)."""
    fulls = [s for s in sources if s[2] == "full"]
    if not fulls:
        raise SystemExit("no baseline (kind=full) bulletin found")
    base = max(fulls, key=_order)
    first = min(fulls, key=_order)
    ordered = sorted((s for s in sources if s[2] in ("full", "delta")), key=_order)
    applied = [s for s in ordered if _order(s) >= _order(first)]
    deltas = [s for s in applied if s[2] == "delta" and _order(s) > _order(base)]

    current: dict[str, PriceRow] = {}
    last: dict[str, PriceRow] = {}  # last readable row of every code ever listed
    listed_in: dict[str, tuple[str, int]] = {}  # last bulletin that listed each code
    gone: set[str] = set()  # withdrawn and not listed since
    history: list[dict] = []
    withdrawn: list[dict] = []
    # one article may carry several files; a full list is all of them together
    for (date, aid, kind), group in groupby(applied, key=lambda s: s[:3]):
        paths = [s[3] for s in group]
        if kind == "full":
            unreadable: list[dict] | None = [] if issues is not None else None
            listed: dict[str, PriceRow] = {}
            for path in paths:
                for r in read_bulletin(path, unreadable):
                    listed[r.eof_code] = r
            bad = [c for c in listed if len(c) != 9 or not c.isdigit()]
            if bad:
                # e.g. a numeric cell that lost its leading zeros: every such
                # code would look withdrawn and then first seen again
                raise ValueError(f"{paths[0].name}: eof_code {bad[0]!r} is not 9 digits")
            if issues is not None:
                issues.extend(unreadable)
            held = {i["eof_code"] for i in unreadable or []}  # listed but unreadable
            for code in sorted(current.keys() | listed.keys()):
                old, new = current.get(code), listed.get(code)
                if new is None:
                    if code in held:
                        continue  # in the review queue; not a withdrawal
                    history += _events(code, "withdrawn", old, None, date, aid)
                    gone.add(code)
                    if (date, aid) == _order(base):
                        withdrawn.append(
                            {
                                "eof_code": code,
                                "name": old.name,
                                "price_retail": old.price_retail,
                                "last_listed": {
                                    "published": listed_in[code][0],
                                    "moh_article": listed_in[code][1],
                                },
                            }
                        )
                    continue
                if old is not None:
                    history += _events(code, "update", old, new, date, aid)
                elif code in gone:
                    history += _events(code, "reappeared", None, new, date, aid)
                    gone.discard(code)
                elif code in last:  # held back by an unreadable row earlier
                    history += _events(code, "update", last[code], new, date, aid)
                else:
                    history += _events(code, "first_seen", None, new, date, aid)
                listed_in[code] = (date, aid)
            last.update(listed)
            current = listed
            if provenance is not None:
                provenance.clear()
                provenance.update({c: {"published": date, "moh_article": aid} for c in listed})
            continue
        for path in paths:
            for r in read_bulletin(path, issues):
                code = r.eof_code
                old = current.get(code)
                if old is not None:
                    history += _events(code, "update", old, r, date, aid)
                    # keep earlier values for fields a delta leaves empty
                    for f in ("atc", "mah"):
                        if not getattr(r, f):
                            setattr(r, f, getattr(old, f))
                    if not r.substances:
                        r.substances = old.substances
                elif code in gone:
                    history += _events(code, "reappeared", None, r, date, aid)
                    gone.discard(code)
                elif code in last:
                    history += _events(code, "update", last[code], r, date, aid)
                else:
                    history += _events(code, "new", None, r, date, aid)
                current[code] = last[code] = r
                listed_in[code] = (date, aid)
                if provenance is not None:
                    provenance[code] = {"published": date, "moh_article": aid}
    if report is not None:
        report["applied"] = applied
        report["skipped"] = [s for s in ordered if s[2] == "delta" and _order(s) < _order(first)]
        report["withdrawn"] = withdrawn
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


PACKAGE_FIELDS = [
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
    "price_published",
    "price_moh_article",
]


def article_index(manifest: list[dict]) -> dict[int, dict]:
    """article_id -> {published, title, url, kind} from a crawl manifest."""
    out = {}
    for art in manifest:
        d, m, y = art["date"].split("/")
        out[art["article_id"]] = {
            "moh_article": art["article_id"],
            "published": f"{y}-{m}-{d}",
            "title": art["title"],
            "url": art["url"],
            "kind": art["kind"],
        }
    return out


def write(
    out: Path,
    base,
    deltas,
    rows: dict[str, PriceRow],
    history,
    problems,
    issues=(),
    provenance: dict[str, dict] | None = None,
    articles: dict[int, dict] | None = None,
    pending: list[dict] | None = None,
    report: dict | None = None,
) -> None:
    """`report` is the dict merge() filled; without it only base + deltas are listed."""
    provenance = provenance or {}
    articles = articles or {}
    report = report or {}
    applied = report.get("applied") or [base, *deltas]
    out.mkdir(parents=True, exist_ok=True)
    with (out / "packages.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(PACKAGE_FIELDS)
        for code in sorted(rows):
            r = rows[code]
            src = provenance.get(code, {})
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
                    src.get("published", ""),
                    src.get("moh_article", ""),
                ]
            )
    with (out / "price_history.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(
            fh, fieldnames=["eof_code", "field", "old", "new", "change", "published", "moh_article"]
        )
        w.writeheader()
        w.writerows(history)
    packages = []
    for c in sorted(rows):
        rec = asdict(rows[c])
        rec["price_source"] = provenance.get(c)
        packages.append(rec)
    (out / "packages.json").write_text(
        json.dumps({"schema_version": SCHEMA_VERSION, "packages": packages}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    sources = {}
    for d in applied:  # one entry per article, in the order applied
        if d[1] not in sources:
            meta = dict(articles.get(d[1], {}))
            meta.update({"moh_article": d[1], "published": d[0], "kind": d[2], "file": d[3].name})
            sources[d[1]] = meta
    (out / "sources.json").write_text(
        json.dumps(list(sources.values()), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    def listing(ss):
        return [{"published": d[0], "moh_article": d[1], "file": d[3].name} for d in ss]

    changes = Counter(h["change"] for h in history)
    (out / "build_report.json").write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "baseline": {"published": base[0], "moh_article": base[1], "file": base[3].name},
                "deltas_applied": [
                    {"published": d[0], "moh_article": d[1], "file": d[3].name} for d in deltas
                ],
                "packages": len(rows),
                "history_events": len(history),
                "validation_problems": problems,
                "review_queue": list(issues),
                "pending_bulletins": pending or [],
                "withdrawn_since_last_full": report.get("withdrawn", []),
                "skipped_attachments": report.get("skipped_attachments", []),
                "history": {
                    "fulls_applied": listing(d for d in applied if d[2] == "full"),
                    "older_deltas_applied": listing(
                        d for d in applied if d[2] == "delta" and (d[0], d[1]) < (base[0], base[1])
                    ),
                    "deltas_before_first_full": listing(report.get("skipped", [])),
                    "events": {c: changes[c] for c in CHANGES},
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def pending_bulletins(sources, base, articles: dict[int, dict]) -> list[dict]:
    """Bulletins known from the listing, published on/after the baseline, not applied.

    Deltas whose files are not downloaded yet, and amendments (never applied
    automatically). Shown publicly so the gap is visible, not hidden."""
    applied = {s[1] for s in sources if (s[0], s[1]) >= (base[0], base[1])}
    out = []
    for aid, a in sorted(articles.items(), key=lambda kv: (kv[1]["published"], kv[0])):
        if a["kind"] not in ("full", "delta", "revision", "amendment") or aid in applied:
            continue
        if (a["published"], aid) <= (base[0], base[1]):
            continue
        out.append(dict(a, reason="not downloaded yet" if a["kind"] == "delta" else "needs manual review"))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", action="store_true", help="offline run on samples/raw only")
    ap.add_argument("--manifest", type=Path)
    ap.add_argument("--raw", type=Path, default=ROOT / "data/raw")
    ap.add_argument("--out", type=Path, default=ROOT / "data/current")
    a = ap.parse_args()

    # The sample bulletins are official files kept in git; they also seed a
    # production build, so a run never depends on the ministry site being up.
    sample_sources = [(d, i, k, ROOT / "samples/raw" / f) for d, i, k, f in SAMPLES]
    articles = article_index(json.loads(SAMPLES_MANIFEST.read_text(encoding="utf-8")))
    skipped: list[dict] = []
    if a.samples:
        sources = sample_sources
    else:
        if not a.manifest:
            ap.error("--manifest or --samples required")
        sources = []
        skipped: list[dict] = []
        if a.manifest.exists():
            manifest = json.loads(a.manifest.read_text(encoding="utf-8"))
            articles.update(article_index(manifest))
            for art in manifest:
                if art["kind"] not in ("full", "delta"):
                    continue
                d, m, y = art["date"].split("/")
                files = [a.raw / f"moh_{art['article_id']}_{att['fdl']}.xlsx" for att in art["attachments"]]
                files = [p for p in files if p.exists()]
                if art["kind"] == "full" and len(files) > 1:
                    # e.g. 10267 (03/2022): the complete list plus a 54-row table of
                    # amendments. Only the complete list is a baseline; the rest is
                    # reported, never read as a full list (it would "withdraw" everything).
                    main_file = max(files, key=lambda p: p.stat().st_size)
                    skipped += [
                        {
                            "moh_article": art["article_id"],
                            "file": p.name,
                            "reason": "secondary table of a full list",
                        }
                        for p in files
                        if p != main_file
                    ]
                    files = [main_file]
                for p in files:
                    sources.append((f"{y}-{m}-{d}", art["article_id"], art["kind"], p))
        have = {s[1] for s in sources}
        sources += [s for s in sample_sources if s[1] not in have]

    issues: list[dict] = []
    provenance: dict[str, dict] = {}
    report: dict = {}
    base, deltas, rows, history = merge(sources, issues, provenance, report)
    report["skipped_attachments"] = skipped
    problems = validate(rows)
    pending = pending_bulletins(sources, base, articles)
    write(a.out, base, deltas, rows, history, problems, issues, provenance, articles, pending, report)
    fulls = len({d[1] for d in report["applied"] if d[2] == "full"})
    print(
        f"baseline {base[3].name}; {len(deltas)} deltas; {len(rows)} packages; "
        f"history from {fulls} full lists, {len(history)} events; "
        f"{len(report['withdrawn'])} withdrawn by the baseline; {len(problems)} validation problems; "
        f"{len(issues)} rows for manual review; {len(pending)} bulletins pending -> {a.out}"
    )


if __name__ == "__main__":
    main()
