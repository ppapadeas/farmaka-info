"""Generate the static site, the open data downloads and the API shards.

Usage:
    python -m sitegen.build --data data/current --out dist

Input: the files written by `python -m ingest.build` (and, if present,
`reimbursement_*.csv` from `python -m ingest.reimbursement`).
Output (served as static assets by the worker in web/worker.js):
    index.html, farmako/<eof_code>/, ousia/<slug>/, dedomena/, sxetika/, 404.html
    search-index.tsv          compact search index (browser and API)
    api/pk/<nn>.json          package records in 256 shards (API)
    data/*                    downloads: csv, json, sqlite, SHA256SUMS
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from . import pages
from .text import skeleton, slugify

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "web" / "static"
SITE = "https://farmaka.info"
SHARDS = 256

REIMBURSEMENT_SOURCE = {
    "publisher": "Υπουργείο Υγείας",
    "title": "Κατάλογος Αποζημιούμενων Φαρμάκων, αναθεώρηση Οκτωβρίου 2026",
    "moh_article": 14875,
    "published": "2026-10-22",
    "url": "https://www.moh.gov.gr/articles/times-farmakwn/epitroph-aksiologhshs-kai-apozhmiwshs-farmakwn",
}


def shard_of(code: str) -> str:
    """Stable shard for an EOF code; web/worker.js uses the same function."""
    h = 0
    for ch in code:
        h = (h * 31 + ord(ch)) % 65536
    return f"{h % SHARDS:02x}"


def _float(v: str | None) -> float | None:
    """CSV cell -> float; price_history stores the non_reimbursed flag as True/False."""
    if v is None or str(v).strip() == "":
        return None
    if str(v) in ("True", "False"):
        return 1.0 if str(v) == "True" else 0.0
    return float(v)


def load_reimbursement(data: Path) -> dict[str, list[dict]]:
    """eof_code -> reimbursement entries from reimbursement_*.csv (optional)."""
    out: dict[str, list[dict]] = defaultdict(list)
    for path in sorted(data.glob("reimbursement_*.csv")):
        with path.open(encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                restrictions = [x for x in (r.get("restrictions") or "").split(" || ") if x.strip()]
                out[r["eof_code"]].append(
                    {
                        "list": r.get("list_name") or path.stem.removeprefix("reimbursement_"),
                        "reimbursement_price": _float(r.get("reimbursement_price")),
                        "retail_in_list": _float(r.get("retail_in_list")),
                        "ddd": _float(r.get("ddd")),
                        "ddd_unit": r.get("ddd_unit") or None,
                        "daily_doses_per_pack": _float(r.get("daily_doses_per_pack")),
                        "daily_cost": _float(r.get("daily_cost")),
                        "dispensing": r.get("dispensing") or None,
                        "route_group": r.get("route_group") or None,
                        "restrictions": restrictions,
                        "flag_s": r.get("flag_s") or None,
                        "flag_t": r.get("flag_t") or None,
                        "atc": r.get("atc") or None,
                        "source": REIMBURSEMENT_SOURCE,
                    }
                )
    return out


def load(data: Path):
    pk = json.loads((data / "packages.json").read_text(encoding="utf-8"))
    packages = pk["packages"] if isinstance(pk, dict) else pk
    sources = json.loads((data / "sources.json").read_text(encoding="utf-8"))
    report = json.loads((data / "build_report.json").read_text(encoding="utf-8"))
    with (data / "price_history.csv").open(encoding="utf-8") as fh:
        history = list(csv.DictReader(fh))
    return packages, sources, report, history


def enrich(packages, sources, history, reimb) -> list[dict]:
    by_article = {s["moh_article"]: s for s in sources}
    hist = defaultdict(list)
    for h in history:
        hist[h["eof_code"]].append(
            {
                "field": h["field"],
                "old": _float(h["old"]),
                "new": _float(h["new"]),
                "change": h["change"],
                "published": h["published"],
                "moh_article": int(h["moh_article"]),
            }
        )
    out = []
    for p in packages:
        src = p.get("price_source") or {}
        art = by_article.get(src.get("moh_article"), {})
        out.append(
            {
                "eof_code": p["eof_code"],
                "barcode": p["barcode"],
                "name": p["name"],
                "atc": p["atc"],
                "substances": p["substances"],
                "mah": p["mah"],
                "vat": p["vat"],
                "non_reimbursed": p["non_reimbursed"],
                "prices": {
                    "exfactory": p["price_exfactory"],
                    "wholesale": p["price_wholesale"],
                    "retail": p["price_retail"],
                },
                "price_source": {
                    "publisher": "Υπουργείο Υγείας",
                    "moh_article": src.get("moh_article"),
                    "published": src.get("published"),
                    "title": art.get("title"),
                    "url": art.get("url"),
                    "file": art.get("file"),
                },
                "price_history": sorted(hist.get(p["eof_code"], []), key=lambda h: h["published"]),
                "reimbursement": reimb.get(p["eof_code"], []),
                "url": f"{SITE}/farmako/{p['eof_code']}/",
            }
        )
    return out


def substance_index(pkgs: list[dict]) -> dict[str, dict]:
    """slug -> {name, codes}; unique slugs even if two INNs fold the same."""
    names = sorted({s for p in pkgs for s in p["substances"]})
    slugs: dict[str, str] = {}
    used: set[str] = set()
    for n in names:
        base = slugify(n)
        slug, i = base, 2
        while slug in used:
            slug, i = f"{base}-{i}", i + 1
        used.add(slug)
        slugs[n] = slug
    out: dict[str, dict] = {}
    for n, slug in slugs.items():
        out[slug] = {"name": n, "slug": slug, "codes": []}
    for p in pkgs:
        for s in p["substances"]:
            out[slugs[s]]["codes"].append(p["eof_code"])
    return out


def write_search_index(pkgs: list[dict], path: Path) -> None:
    def clean(s: str) -> str:
        return str(s or "").replace("\t", " ").replace("\n", " ").strip()

    lines = ["# farmaka.info search index v1; columns: see web/static/search-core.js COLUMNS"]
    for p in pkgs:
        subst = "|".join(p["substances"])
        lines.append(
            "\t".join(
                [
                    p["eof_code"],
                    p["barcode"],
                    clean(p["name"]),
                    clean(subst),
                    clean(p["mah"]),
                    f"{p['prices']['retail']:.2f}",
                    "1" if p["non_reimbursed"] else "0",
                    "1" if p["reimbursement"] else "0",
                    skeleton(p["name"]),
                    skeleton(" ".join(p["substances"])),
                    skeleton(p["mah"]),
                ]
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_sqlite(pkgs: list[dict], history, sources, path: Path) -> None:
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE source (moh_article INTEGER PRIMARY KEY, published TEXT, kind TEXT,
            title TEXT, url TEXT, file TEXT);
        CREATE TABLE package (eof_code TEXT PRIMARY KEY, barcode TEXT UNIQUE, name TEXT, atc TEXT,
            substances TEXT, mah TEXT, vat REAL, non_reimbursed INTEGER, price_exfactory REAL,
            price_wholesale REAL, price_retail REAL, price_published TEXT,
            price_moh_article INTEGER REFERENCES source(moh_article));
        CREATE TABLE price_event (eof_code TEXT REFERENCES package(eof_code), field TEXT, old REAL,
            new REAL, change TEXT, published TEXT, moh_article INTEGER REFERENCES source(moh_article));
        CREATE TABLE reimbursement (eof_code TEXT REFERENCES package(eof_code), list TEXT,
            reimbursement_price REAL, retail_in_list REAL, ddd REAL, ddd_unit TEXT,
            daily_doses_per_pack REAL, daily_cost REAL, dispensing TEXT, route_group TEXT,
            restrictions TEXT, flag_s TEXT, flag_t TEXT, published TEXT, moh_article INTEGER);
        CREATE INDEX price_event_code ON price_event(eof_code);
        """
    )
    con.executemany(
        "INSERT INTO source VALUES (?,?,?,?,?,?)",
        [
            (s["moh_article"], s["published"], s["kind"], s.get("title"), s.get("url"), s.get("file"))
            for s in sources
        ],
    )
    con.executemany(
        "INSERT INTO package VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                p["eof_code"],
                p["barcode"],
                p["name"],
                p["atc"],
                "|".join(p["substances"]),
                p["mah"],
                p["vat"],
                int(p["non_reimbursed"]),
                p["prices"]["exfactory"],
                p["prices"]["wholesale"],
                p["prices"]["retail"],
                p["price_source"]["published"],
                p["price_source"]["moh_article"],
            )
            for p in pkgs
        ],
    )
    con.executemany(
        "INSERT INTO price_event VALUES (?,?,?,?,?,?,?)",
        [
            (
                h["eof_code"],
                h["field"],
                _float(h["old"]),
                _float(h["new"]),
                h["change"],
                h["published"],
                h["moh_article"],
            )
            for h in history
        ],
    )
    con.executemany(
        "INSERT INTO reimbursement VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                p["eof_code"],
                r["list"],
                r["reimbursement_price"],
                r["retail_in_list"],
                r["ddd"],
                r["ddd_unit"],
                r["daily_doses_per_pack"],
                r["daily_cost"],
                r["dispensing"],
                r["route_group"],
                " || ".join(r["restrictions"]),
                r["flag_s"],
                r["flag_t"],
                r["source"]["published"],
                r["source"]["moh_article"],
            )
            for p in pkgs
            for r in p["reimbursement"]
        ],
    )
    con.commit()
    con.execute("VACUUM")
    con.close()


def write_reimbursement_csv(pkgs: list[dict], path: Path) -> int:
    cols = [
        "eof_code",
        "list",
        "reimbursement_price",
        "retail_in_list",
        "ddd",
        "ddd_unit",
        "daily_doses_per_pack",
        "daily_cost",
        "dispensing",
        "route_group",
        "restrictions",
        "flag_s",
        "flag_t",
        "published",
        "moh_article",
    ]
    n = 0
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for p in pkgs:
            for r in p["reimbursement"]:
                n += 1
                w.writerow(
                    [p["eof_code"]]
                    + [
                        " || ".join(r[c]) if c == "restrictions" else ("" if r.get(c) is None else r[c])
                        for c in cols[1:13]
                    ]
                    + [r["source"]["published"], r["source"]["moh_article"]]
                )
    return n


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(data: Path, out: Path, built_at: str) -> dict:
    packages, sources, report, history = load(data)
    reimb = load_reimbursement(data)
    pkgs = enrich(packages, sources, history, reimb)
    by_code = {p["eof_code"]: p for p in pkgs}
    subst = substance_index(pkgs)
    slug_of = {v["name"]: k for k, v in subst.items()}

    if out.exists():
        shutil.rmtree(out)
    (out / "static").mkdir(parents=True)
    for f in STATIC.iterdir():
        if f.is_file():
            shutil.copy2(f, out / "static" / f.name)
    shutil.copy2(STATIC / "favicon.svg", out / "favicon.svg")

    latest = max(s["published"] for s in sources)
    meta = {
        "schema_version": report.get("schema_version", 1),
        "built_at": built_at,
        "data_published": latest,
        "packages": len(pkgs),
        "reimbursed_packages": sum(1 for p in pkgs if p["reimbursement"]),
        "substances": len(subst),
        "sources": sources,
        "reimbursement_source": REIMBURSEMENT_SOURCE if reimb else None,
        "pending_bulletins": report.get("pending_bulletins", []),
        "validation_problems": len(report.get("validation_problems", [])),
        "review_queue": report.get("review_queue", []),
        "license": {"data": "CC0-1.0", "code": "AGPL-3.0-or-later"},
    }

    # downloads
    dl = out / "data"
    dl.mkdir()
    for name in ("packages.csv", "price_history.csv", "sources.json", "build_report.json"):
        shutil.copy2(data / name, dl / name)
    (dl / "farmaka.json").write_text(
        json.dumps({"meta": meta, "packages": pkgs}, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    write_reimbursement_csv(pkgs, dl / "reimbursement.csv")
    write_sqlite(pkgs, history, sources, dl / "farmaka.sqlite")
    files = sorted(p for p in dl.iterdir() if p.is_file())
    (dl / "SHA256SUMS").write_text("".join(f"{sha256(p)}  {p.name}\n" for p in files), encoding="utf-8")
    downloads = [{"name": p.name, "bytes": p.stat().st_size, "sha256": sha256(p)} for p in files]

    # API: meta + package shards + substances
    api = out / "api"
    (api / "pk").mkdir(parents=True)
    (api / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    shards: dict[str, dict] = defaultdict(dict)
    for p in pkgs:
        shards[shard_of(p["eof_code"])][p["eof_code"]] = p
    for k, v in shards.items():
        (api / "pk" / f"{k}.json").write_text(
            json.dumps(v, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )
    (api / "substances.json").write_text(
        json.dumps(
            {k: {"name": v["name"], "eof_codes": v["codes"]} for k, v in sorted(subst.items())},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    write_search_index(pkgs, out / "search-index.tsv")

    # pages
    ctx = pages.Context(meta=meta, by_code=by_code, subst=subst, slug_of=slug_of, downloads=downloads)
    urls = ["/", "/dedomena/", "/sxetika/"]
    (out / "index.html").write_text(pages.home(ctx, history), encoding="utf-8")
    for path, html in (
        ("dedomena", pages.data_page(ctx)),
        ("sxetika", pages.about(ctx)),
    ):
        (out / path).mkdir()
        (out / path / "index.html").write_text(html, encoding="utf-8")
    (out / "404.html").write_text(pages.not_found(ctx), encoding="utf-8")
    (out / "_templates").mkdir()
    (out / "_templates" / "search.html").write_text(pages.search_template(ctx), encoding="utf-8")
    for p in pkgs:
        d = out / "farmako" / p["eof_code"]
        d.mkdir(parents=True)
        (d / "index.html").write_text(pages.package(ctx, p), encoding="utf-8")
        urls.append(f"/farmako/{p['eof_code']}/")
    for slug, s in subst.items():
        d = out / "ousia" / slug
        d.mkdir(parents=True)
        (d / "index.html").write_text(pages.substance(ctx, s), encoding="utf-8")
        urls.append(f"/ousia/{slug}/")

    (out / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(f"<url><loc>{SITE}{u}</loc></url>\n" for u in urls)
        + "</urlset>\n",
        encoding="utf-8",
    )
    (out / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\nDisallow: /_templates/\nSitemap: {SITE}/sitemap.xml\n", encoding="utf-8"
    )
    (out / "_headers").write_text(pages.HEADERS, encoding="utf-8")
    (out / "manifest.webmanifest").write_text(pages.webmanifest(), encoding="utf-8")
    n_files = sum(1 for _ in out.rglob("*") if _.is_file())
    return {"packages": len(pkgs), "substances": len(subst), "files": n_files}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=ROOT / "data/current")
    ap.add_argument("--out", type=Path, default=ROOT / "dist")
    ap.add_argument("--built-at", help="ISO timestamp to stamp the build (default: now, UTC)")
    a = ap.parse_args()
    built_at = a.built_at or datetime.now(UTC).replace(microsecond=0).isoformat()
    r = build(a.data, a.out, built_at)
    print(f"{r['packages']} packages, {r['substances']} substances, {r['files']} files -> {a.out}")


if __name__ == "__main__":
    main()
