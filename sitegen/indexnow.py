"""IndexNow: after a deploy, tell the search engines that take part (Bing, Yandex, Seznam,
Naver and others share what they receive) which pages changed, instead of waiting for them
to read the sitemap again (Δ-26).

Changed means new in the sitemap, or with another lastmod than in the sitemap that was live
before the deploy. Only public addresses of the site are sent, nothing else. The key is not
a secret: the protocol has it served at /<key>.txt to show that the site sends the list.

    python -m sitegen.indexnow --old old_sitemap.xml --new dist/sitemap.xml [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path

SITE = "https://farmaka.info"
KEY = "e4cbee29c9d5bb3520230a3279e338b4"
ENDPOINT = "https://api.indexnow.org/indexnow"
BATCH = 10_000  # most addresses the protocol takes in one request
USER_AGENT = "farmaka.info IndexNow (+https://farmaka.info/sxetika/)"
_NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def read_sitemap(text: str) -> dict[str, str | None]:
    """loc -> lastmod (None where there is none)."""
    root = ET.fromstring(text)
    return {
        u.findtext("s:loc", namespaces=_NS).strip(): (u.findtext("s:lastmod", namespaces=_NS) or None)
        for u in root.findall("s:url", _NS)
    }


def changed(old: dict[str, str | None], new: dict[str, str | None]) -> list[str]:
    """Addresses of `new` that `old` lacks or that have another lastmod, in sitemap order."""
    return [u for u, d in new.items() if u not in old or old[u] != d]


def submit(urls: list[str], post: Callable | None = None, log=print) -> bool:
    """Send the addresses in batches; False at the first answer that is not 200/202."""
    if post is None:
        import requests

        post = requests.post
    for i in range(0, len(urls), BATCH):
        batch = urls[i : i + BATCH]
        r = post(
            ENDPOINT,
            json={
                "host": SITE.removeprefix("https://"),
                "key": KEY,
                "keyLocation": f"{SITE}/{KEY}.txt",
                "urlList": batch,
            },
            headers={"User-Agent": USER_AGENT},
            timeout=60,
        )
        log(f"IndexNow: {len(batch)} addresses, answer {r.status_code}")
        if r.status_code not in (200, 202):
            return False
    return True


def main(argv: list[str] | None = None, post: Callable | None = None, log=print) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", type=Path, required=True, help="the sitemap that was live before the deploy")
    ap.add_argument("--new", type=Path, required=True, help="the sitemap just deployed")
    ap.add_argument("--dry-run", action="store_true", help="list what would be sent, send nothing")
    a = ap.parse_args(argv)
    try:
        old = read_sitemap(a.old.read_text(encoding="utf-8"))
    except (OSError, ET.ParseError) as err:
        # without the previous list everything would look new: better send nothing today
        log(f"IndexNow: no previous sitemap ({err}), nothing sent")
        return 0
    urls = changed(old, read_sitemap(a.new.read_text(encoding="utf-8")))
    if not urls:
        log("IndexNow: nothing changed")
        return 0
    if a.dry_run:
        log(f"IndexNow: {len(urls)} addresses would be sent, e.g. {urls[:3]}")
        return 0
    # a refusal is reported but does not fail the deploy: the sitemap still has everything
    submit(urls, post, log)
    return 0


if __name__ == "__main__":
    sys.exit(main())
