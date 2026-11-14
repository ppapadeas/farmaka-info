"""Crawl MoH bulletin listings, record a manifest, download spreadsheets.

Usage:
    python -m ingest.fetch --section prices --out data            # manifest + xlsx
    python -m ingest.fetch --section reimbursement --out data --pdf

The manifest is written even if the crawl stops half way; already downloaded
files are skipped on the next run, so re-running resumes.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import requests

from .moh import PRICE_BULLETINS, REIMBURSEMENT, Client, write_manifest

SECTIONS = {"prices": PRICE_BULLETINS, "reimbursement": REIMBURSEMENT}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--section", choices=SECTIONS, default="prices")
    ap.add_argument("--out", type=Path, default=Path("data"))
    ap.add_argument("--max-pages", type=int, default=50)
    ap.add_argument("--list-only", action="store_true", help="manifest of listings, no article pages")
    ap.add_argument("--pdf", action="store_true", help="also download PDF attachments")
    a = ap.parse_args()

    a.out.mkdir(parents=True, exist_ok=True)
    manifest = a.out / f"manifest_{a.section}.json"
    c = Client()
    arts = c.list_articles(SECTIONS[a.section], a.max_pages)
    print(f"{len(arts)} articles", flush=True)
    failures = 0
    try:
        if not a.list_only:
            for n, art in enumerate(arts, 1):
                if a.section == "prices" and art.kind not in ("full", "revision", "delta", "amendment"):
                    continue
                try:
                    c.load_attachments(art)
                    for att in art.attachments:
                        if att.ext == "xlsx" or (a.pdf and att.ext == "pdf"):
                            c.download(att, a.out / "raw", art)
                except requests.RequestException as e:
                    failures += 1
                    print(
                        f"[{n}/{len(arts)}] article {art.article_id} failed: {e}", file=sys.stderr, flush=True
                    )
                if n % 25 == 0:
                    write_manifest(arts, manifest)
                    print(f"[{n}/{len(arts)}]", flush=True)
    finally:
        write_manifest(arts, manifest)
    print(f"done; {failures} articles failed (re-run to resume)")


if __name__ == "__main__":
    main()
