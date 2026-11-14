"""Crawl MoH bulletin listings, record a manifest, download spreadsheets.

Usage:
    python -m ingest.fetch --section prices --out data --max-pages 2   # daily
    python -m ingest.fetch --section prices --out data --backfill full # once
    python -m ingest.fetch --section reimbursement --out data --pdf
    python -m ingest.fetch --section otc --out data --max-pages 2      # daily, ΜΗ.ΣΥ.ΦΑ.

Incremental and polite:
- the manifest from earlier runs is kept; articles that dropped off the first
  listing pages stay in it, so the baseline is never lost;
- an article page is fetched only if its files are not already on disk;
- if no complete list (kind "full" for prices, a complete ΜΗ.ΣΥ.ΦΑ. list for
  otc) is known yet, listing continues page by page (up to --baseline-pages)
  until one is found;
- the daily run fetches only the articles on the pages it reads and those from
  the newest known complete list on; older articles are history (see below).
The manifest is written even if the crawl stops half way; re-running resumes.

Section otc (non-prescription medicines, ΜΗ.ΣΥ.ΦΑ.): titles are classified by
moh.classify_otc; an article's xlsx are downloaded, its pdf only if it has no
xlsx. Manifest data/manifest_otc.json; the list is built by ingest.otc.

Backfill (`--backfill full`, run once): reads every listing page (about 18),
one request at a time, until a page adds nothing new, and downloads only the
xlsx of the complete price lists. Same manifest, same files, same resume rules
as the daily run. Exits with status 1 if anything was left undone.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import requests

from .moh import (
    OTC_BULLETINS,
    PRICE_BULLETINS,
    REIMBURSEMENT,
    Article,
    Client,
    classify,
    classify_otc,
    read_manifest,
    write_manifest,
)
from .otc import OTC_BASELINE_KINDS, OTC_KINDS

SECTIONS = {"prices": PRICE_BULLETINS, "reimbursement": REIMBURSEMENT, "otc": OTC_BULLETINS}
CLASSIFIERS = {"otc": classify_otc}  # every other section: moh.classify
PRICE_KINDS = ("full", "revision", "delta", "amendment")
# kinds whose files a section downloads (a section not listed: every article)
FETCH_KINDS = {"prices": PRICE_KINDS, "otc": OTC_KINDS}
# complete lists: the daily run fetches every article from the newest one on
BASELINE_KINDS = {"prices": ("full",), "otc": OTC_BASELINE_KINDS}
BACKFILL_KINDS = ("full",)


def wanted(art: Article, pdf: bool, pdf_if_no_xlsx: bool = False) -> list:
    """Attachments to download: the xlsx, plus the pdf with `pdf`, or the pdf
    instead with `pdf_if_no_xlsx` when the article has no xlsx at all."""
    xlsx = [a for a in art.attachments if a.ext == "xlsx"]
    if pdf_if_no_xlsx and not xlsx:
        return [a for a in art.attachments if a.ext == "pdf"]
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


def list_all(c: Client, section: str, max_pages: int, kind_of=classify) -> tuple[list[Article], str | None]:
    """Every listing page, one at a time, until a page adds nothing new.

    Returns what was listed and, if listing stopped early, why; what was listed
    is kept either way, so a re-run resumes."""
    out: list[Article] = []
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        try:
            items = c.list_articles(section, 1, first_page=page, kind_of=kind_of)
        except requests.RequestException as e:
            return out, f"listing page {page} failed: {e}"
        more = [x for x in items if x.url not in seen]
        if not more:
            return out, None
        seen.update(x.url for x in more)
        out += more
    return out, f"listing stopped at --max-pages {max_pages}; there may be more pages"


def daily_scope(
    arts: list[Article], listed: list[Article], baseline: tuple[str, ...] = ("full",)
) -> list[Article]:
    """Articles the daily run may fetch: those on the pages it just read and all
    from the newest known complete list (a kind in `baseline`) on: what the
    current prices need.

    Without this, a manifest filled by --backfill would turn the next daily run
    into a crawl of the whole history."""
    fulls = [(a.iso_date, a.article_id) for a in arts if a.kind in baseline]
    if not fulls:
        return arts
    newest = max(fulls)
    on_pages = {a.article_id for a in listed}
    return [a for a in arts if a.article_id in on_pages or (a.iso_date, a.article_id) >= newest]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--section", choices=SECTIONS, default="prices")
    ap.add_argument("--out", type=Path, default=Path("data"))
    ap.add_argument("--max-pages", type=int, default=50)
    ap.add_argument("--baseline-pages", type=int, default=10, help="max pages to look for a baseline")
    ap.add_argument("--list-only", action="store_true", help="manifest of listings, no article pages")
    ap.add_argument("--pdf", action="store_true", help="also download PDF attachments")
    ap.add_argument(
        "--backfill",
        choices=BACKFILL_KINDS,
        help="once: list every page (up to --max-pages) and download only articles of this kind",
    )
    a = ap.parse_args()
    if a.backfill and a.section != "prices":
        ap.error("--backfill works only with --section prices")

    a.out.mkdir(parents=True, exist_ok=True)
    raw = a.out / "raw"
    manifest = a.out / f"manifest_{a.section}.json"
    previous = read_manifest(manifest)
    c = Client()
    kind_of = CLASSIFIERS.get(a.section, classify)
    baseline = BASELINE_KINDS.get(a.section)
    problem = None
    if a.backfill:
        listed, problem = list_all(c, SECTIONS[a.section], a.max_pages)
    else:
        listed = c.list_articles(SECTIONS[a.section], a.max_pages, kind_of=kind_of)
    if baseline and not a.backfill:
        page = a.max_pages + 1
        while not any(x.kind in baseline for x in listed + previous) and page <= a.baseline_pages and listed:
            more = c.list_articles(SECTIONS[a.section], 1, first_page=page, kind_of=kind_of)
            if not more:
                break
            listed += [m for m in more if m.article_id not in {x.article_id for x in listed}]
            page += 1
    arts = merge_manifests(listed, previous)
    if problem:
        print(problem, file=sys.stderr, flush=True)
    print(f"{len(listed)} articles listed, {len(arts)} in manifest", flush=True)
    if a.backfill:
        todo = [x for x in arts if x.kind == a.backfill]
    elif a.section in FETCH_KINDS:
        todo = [x for x in daily_scope(arts, listed, baseline) if x.kind in FETCH_KINDS[a.section]]
    else:
        todo = arts
    pdf_if_no_xlsx = a.section == "otc"
    failures = fetched = 0
    try:
        if not a.list_only:
            for n, art in enumerate(todo, 1):
                if (art.checked or art.attachments) and all(
                    (raw / f"moh_{art.article_id}_{x.fdl}.{x.ext or 'bin'}").exists()
                    for x in wanted(art, a.pdf, pdf_if_no_xlsx)
                ):
                    continue  # already have everything this article offers
                try:
                    if not (art.checked or art.attachments):
                        c.load_attachments(art)
                        art.checked = True
                    for att in wanted(art, a.pdf, pdf_if_no_xlsx):
                        c.download(att, raw, art)
                    fetched += 1
                except requests.RequestException as e:
                    failures += 1
                    print(
                        f"[{n}/{len(todo)}] article {art.article_id} failed: {e}", file=sys.stderr, flush=True
                    )
                if fetched and fetched % 25 == 0:
                    write_manifest(arts, manifest)
                    print(f"[{n}/{len(todo)}]", flush=True)
    finally:
        write_manifest(arts, manifest)
    print(f"done; {fetched} articles fetched, {failures} failed (re-run to resume)")
    if a.backfill:
        no_xlsx = [x for x in todo if x.checked and not wanted(x, False)]
        for x in no_xlsx:
            exts = sorted({att.ext or "?" for att in x.attachments}) or ["none"]
            print(
                f"article {x.article_id} ({x.date}) has no xlsx; attachments: {', '.join(exts)}",
                file=sys.stderr,
            )
        print(f"{len(todo)} articles of kind {a.backfill!r} known, {len(no_xlsx)} without xlsx")
        if problem or failures:
            sys.exit(1)


if __name__ == "__main__":
    main()
