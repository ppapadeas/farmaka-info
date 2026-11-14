"""Crawl MoH bulletin listings, record a manifest, download spreadsheets.

Usage:
    python -m ingest.fetch --section prices --out data --max-pages 2   # daily
    python -m ingest.fetch --section reimbursement --out data --pdf

Incremental and polite:
- the manifest from earlier runs is kept; articles that dropped off the first
  listing pages stay in it, so the baseline is never lost;
- an article page is fetched only if its files are not already on disk;
- if no complete price list (kind "full") is known yet, listing continues page
  by page (up to --baseline-pages) until one is found.
The manifest is written even if the crawl stops half way; re-running resumes.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import requests

from .moh import PRICE_BULLETINS, REIMBURSEMENT, Article, Client, read_manifest, write_manifest

SECTIONS = {"prices": PRICE_BULLETINS, "reimbursement": REIMBURSEMENT}
PRICE_KINDS = ("full", "revision", "delta", "amendment")


def wanted(art: Article, pdf: bool) -> list:
    return [a for a in art.attachments if a.ext == "xlsx" or (pdf and a.ext == "pdf")]


def merge_manifests(listed: list[Article], previous: list[Article]) -> list[Article]:
    """Union by article_id; listing wins for metadata, previous keeps attachments."""
    prev = {a.article_id: a for a in previous}
    out = {}
    for a in listed:
        old = prev.get(a.article_id)
        if old is not None and not a.attachments:
            a.attachments = old.attachments
            a.checked = old.checked
        out[a.article_id] = a
    for aid, a in prev.items():
        out.setdefault(aid, a)
    return sorted(out.values(), key=lambda a: (a.iso_date, a.article_id), reverse=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--section", choices=SECTIONS, default="prices")
    ap.add_argument("--out", type=Path, default=Path("data"))
    ap.add_argument("--max-pages", type=int, default=50)
    ap.add_argument("--baseline-pages", type=int, default=10, help="max pages to look for a baseline")
    ap.add_argument("--list-only", action="store_true", help="manifest of listings, no article pages")
    ap.add_argument("--pdf", action="store_true", help="also download PDF attachments")
    a = ap.parse_args()

    a.out.mkdir(parents=True, exist_ok=True)
    raw = a.out / "raw"
    manifest = a.out / f"manifest_{a.section}.json"
    previous = read_manifest(manifest)
    c = Client()
    listed = c.list_articles(SECTIONS[a.section], a.max_pages)
    if a.section == "prices":
        page = a.max_pages + 1
        while not any(x.kind == "full" for x in listed + previous) and page <= a.baseline_pages and listed:
            more = c.list_articles(SECTIONS[a.section], 1, first_page=page)
            if not more:
                break
            listed += [m for m in more if m.article_id not in {x.article_id for x in listed}]
            page += 1
    arts = merge_manifests(listed, previous)
    print(f"{len(listed)} articles listed, {len(arts)} in manifest", flush=True)
    failures = fetched = 0
    try:
        if not a.list_only:
            for n, art in enumerate(arts, 1):
                if a.section == "prices" and art.kind not in PRICE_KINDS:
                    continue
                if (art.checked or art.attachments) and all(
                    (raw / f"moh_{art.article_id}_{x.fdl}.{x.ext or 'bin'}").exists()
                    for x in wanted(art, a.pdf)
                ):
                    continue  # already have everything this article offers
                try:
                    if not (art.checked or art.attachments):
                        c.load_attachments(art)
                        art.checked = True
                    for att in wanted(art, a.pdf):
                        c.download(att, raw, art)
                    fetched += 1
                except requests.RequestException as e:
                    failures += 1
                    print(
                        f"[{n}/{len(arts)}] article {art.article_id} failed: {e}", file=sys.stderr, flush=True
                    )
                if fetched and fetched % 25 == 0:
                    write_manifest(arts, manifest)
                    print(f"[{n}/{len(arts)}]", flush=True)
    finally:
        write_manifest(arts, manifest)
    print(f"done; {fetched} articles fetched, {failures} failed (re-run to resume)")


if __name__ == "__main__":
    main()
