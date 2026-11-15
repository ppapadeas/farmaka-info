"""«Τι άλλαξε»: recent changes from the price bulletins and the EOF shortage lists.

Changes are grouped by publication date, newest first, under the bulletin or list
they come from. The same data feeds /allages/, its RSS feed and data/allages.csv.
Only what the sources say: price_history.csv events (the first full list we hold is
the baseline, so its `first_seen` rows are not changes) and the EOF lists in
shortages.csv (the date of a list is the date printed on it)."""

from __future__ import annotations

import csv
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from email.utils import format_datetime
from html import escape
from pathlib import Path

from .text import date_gr, eur, num

WINDOW_DAYS = 60
EOF_LISTS = ("limited_availability", "shortage", "discontinuation")
# price_history.csv `change` -> change shown here (retail price only)
FROM_HISTORY = {
    "update": "price",
    "new": "new",
    "first_seen": "new",
    "reappeared": "reappeared",
    "withdrawn": "withdrawn",
}
# display order inside a bulletin; "cut"/"rise" split the price changes
CATEGORIES = ("cut", "rise", "new", "reappeared", "withdrawn", *EOF_LISTS)
TITLES = {
    "cut": ("Μειώσεις τιμής", "μείωση τιμής", "μειώσεις τιμής"),
    "rise": ("Αυξήσεις τιμής", "αύξηση τιμής", "αυξήσεις τιμής"),
    "new": ("Νέες συσκευασίες", "νέα συσκευασία", "νέες συσκευασίες"),
    "reappeared": ("Ξανά στο δελτίο", "ξανά στο δελτίο", "ξανά στο δελτίο"),
    "withdrawn": ("Βγήκαν από το δελτίο", "βγήκε από το δελτίο", "βγήκαν από το δελτίο"),
}
LIST_TITLES = {
    "limited_availability": "Λίστα περιορισμένης διαθεσιμότητας",
    "shortage": "Ανακοίνωση έλλειψης",
    "discontinuation": "Οριστική διακοπή κυκλοφορίας",
}
CSV_COLUMNS = [
    "published",
    "publisher",
    "source_id",
    "source_title",
    "source_url",
    "change",
    "eof_code",
    "name",
    "old",
    "new",
    "pct",
]


def _f(v) -> float | None:
    return None if v in ("", None) else float(v)


def pct_change(old: float | None, new: float | None) -> float | None:
    if not old or new is None:
        return None
    return (new - old) / old * 100


def category(item: dict) -> str:
    if item["change"] == "price":
        return "cut" if item["new"] < item["old"] else "rise"
    return item["change"]


def _sort_key(item: dict) -> tuple:
    c = category(item)
    p = pct_change(item["old"], item["new"])
    # biggest price changes first; everything else by name
    mag = -abs(p) if c in ("cut", "rise") and p is not None else 0.0
    return (CATEGORIES.index(c), mag, item["name"], item["eof_code"])


def collect(
    history: list[dict],
    sources: list[dict],
    names: dict[str, str],
    shortages_csv: Path | None = None,
    pending: list[dict] | None = None,
    days: int = WINDOW_DAYS,
) -> dict:
    """{"start", "end", "days": [{"date", "docs": [doc, ...]}, ...]}, newest first.

    A doc is one bulletin or EOF list: publisher ("moh"/"eof"), id (moh_article),
    title, url, kind, baseline (the first full list: nothing to compare with),
    pending (listed by the ministry, not applied yet) and items
    ({change, eof_code, name, old, new}). The window is the `days` days up to the
    newest date of any source, so a quiet month does not empty the page."""
    fulls = [s for s in sources if s.get("kind") == "full"]
    first = min(fulls, key=lambda s: (s["published"], s["moh_article"]))["moh_article"] if fulls else None
    docs: dict[tuple, dict] = {}

    def doc(key: tuple, published: str, **kw) -> dict:
        if key not in docs:
            base = {"publisher": key[0], "id": None, "title": "", "url": None, "kind": None}
            docs[key] = base | {"baseline": False, "pending": False, "published": published, "items": []} | kw
        return docs[key]

    for s in sources:
        doc(
            ("moh", s["moh_article"]),
            s["published"],
            id=s["moh_article"],
            title=s.get("title") or s.get("file") or "",
            url=s.get("url"),
            kind=s.get("kind"),
            baseline=s["moh_article"] == first,
        )
    for s in pending or []:
        doc(
            ("moh", s["moh_article"]),
            s["published"],
            id=s["moh_article"],
            title=s.get("title") or "",
            url=s.get("url"),
            kind=s.get("kind"),
            pending=True,
        )
    lists: list[dict] = []
    if shortages_csv is not None and shortages_csv.exists():
        with shortages_csv.open(encoding="utf-8") as fh:
            lists = [r for r in csv.DictReader(fh) if r["list_type"] in EOF_LISTS and r.get("published")]
    for r in lists:
        d = doc(
            ("eof", r["list_type"], r["published"], r.get("source_url") or ""),
            r["published"],
            title=r.get("source_title") or "",
            url=r.get("source_url") or None,
            kind=r["list_type"],
        )
        code = r.get("eof_code") or ""
        name = names.get(code) or r.get("name") or ""
        d["items"].append(
            {"change": r["list_type"], "eof_code": code, "name": name, "old": None, "new": None}
        )
    if not docs:
        return {"start": None, "end": None, "days": []}

    end = max(d["published"] for d in docs.values())
    start = (date.fromisoformat(end) - timedelta(days=days)).isoformat()
    for h in history:
        if h["field"] != "price_retail" or h["published"] < start:
            continue
        change = FROM_HISTORY.get(h["change"])
        art = int(h["moh_article"])
        if change is None or (h["change"] == "first_seen" and art == first):
            continue
        old, new = _f(h["old"]), _f(h["new"])
        if change == "price" and (old is None or new is None or old == new):
            continue
        d = doc(("moh", art), h["published"], id=art)  # every applied article is in sources.json
        code = h["eof_code"]
        d["items"].append(
            {"change": change, "eof_code": code, "name": names.get(code, ""), "old": old, "new": new}
        )

    by_day: dict[str, list[dict]] = defaultdict(list)
    for d in docs.values():
        if d["published"] >= start:
            d["items"].sort(key=_sort_key)
            by_day[d["published"]].append(d)
    order = {"moh": 0, "eof": 1}
    out = []
    for day in sorted(by_day, reverse=True):
        ds = sorted(
            by_day[day], key=lambda d: (order[d["publisher"]], d["pending"], d["id"] or 0, d["kind"] or "")
        )
        out.append({"date": day, "docs": ds})
    return {"start": start, "end": end, "days": out}


def counts(docs: list[dict]) -> dict[str, int]:
    n: dict[str, int] = defaultdict(int)
    for d in docs:
        for it in d["items"]:
            n[category(it)] += 1
    return n


def summary(docs: list[dict]) -> str:
    """'2 μειώσεις τιμής, 70 νέες συσκευασίες, λίστα περιορισμένης διαθεσιμότητας (344 φάρμακα)'."""
    n = counts(docs)
    parts = [f"{num(n[c], 0)} {TITLES[c][1] if n[c] == 1 else TITLES[c][2]}" for c in TITLES if n[c]]
    for d in docs:
        if d["publisher"] == "eof":
            k = len(d["items"])
            parts.append(
                f"{LIST_TITLES[d['kind']].lower()} ΕΟΦ ({num(k, 0)} {'φάρμακο' if k == 1 else 'φάρμακα'})"
            )
    if not parts and any(d["pending"] for d in docs):
        parts.append("δελτία σε αναμονή")
    return ", ".join(parts) or "καμία αλλαγή λιανικής τιμής"


def write_csv(changes: dict, path: Path) -> int:
    n = 0
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(CSV_COLUMNS)
        for g in changes["days"]:
            for d in g["docs"]:
                for it in d["items"]:
                    p = pct_change(it["old"], it["new"]) if it["change"] == "price" else None
                    w.writerow(
                        [
                            g["date"],
                            d["publisher"],
                            d["id"] or "",
                            d["title"],
                            d["url"] or "",
                            it["change"],
                            it["eof_code"],
                            it["name"],
                            "" if it["old"] is None else f"{it['old']:.2f}",
                            "" if it["new"] is None else f"{it['new']:.2f}",
                            "" if p is None else f"{p:.1f}",
                        ]
                    )
                    n += 1
    return n


def pct_label(old: float | None, new: float | None) -> str:
    """'−33,5%' / '+0,2%': one decimal, so that a small rise never reads '+0%'."""
    p = pct_change(old, new)
    if p is None:
        return ""
    return f"{'−' if p < 0 else '+'}{num(abs(p), 1)}%"


def _item_text(it: dict) -> str:
    name = it["name"] or f"κωδικός ΕΟΦ {it['eof_code']}"
    if it["change"] == "price":
        p = pct_label(it["old"], it["new"])
        rel = f" ({p})" if p else ""
        return f"{name}: {eur(it['old'])} → {eur(it['new'])}{rel}"
    if it["change"] in ("new", "reappeared"):
        return f"{name}: {eur(it['new'])}"
    if it["change"] == "withdrawn":
        return f"{name} (τελευταία τιμή {eur(it['old'])})"
    return name


def rss(changes: dict, built_at: str, site: str, per_category: int = 10) -> str:
    """RSS 2.0, one item per publication date, with absolute links."""
    page = f"{site}/allages/"
    items = []
    for g in changes["days"]:
        link = f"{page}#d-{g['date']}"
        y, m, d = (int(x) for x in g["date"].split("-"))
        html = []
        for doc in g["docs"]:
            who = "Υπουργείο Υγείας" if doc["publisher"] == "moh" else "ΕΟΦ"
            title = escape(doc["title"] or who)
            src = f'<a href="{escape(doc["url"])}">{title}</a>' if doc["url"] else title
            note = " (σε αναμονή, δεν έχει ενσωματωθεί)" if doc["pending"] else ""
            html.append(f"<p>{who}: {src}{note}</p>")
            by: dict[str, list[dict]] = defaultdict(list)
            for it in doc["items"]:
                by[category(it)].append(it)
            for c in CATEGORIES:
                if not by[c]:
                    continue
                head = TITLES[c][0] if c in TITLES else LIST_TITLES[c]
                lis = "".join(f"<li>{escape(_item_text(it))}</li>" for it in by[c][:per_category])
                rest = len(by[c]) - per_category
                more = f'<p><a href="{link}">και άλλες {num(rest, 0)}</a></p>' if rest > 0 else ""
                html.append(f"<p><b>{head}</b> ({num(len(by[c]), 0)})</p><ul>{lis}</ul>{more}")
        items.append(
            "<item>"
            f"<title>{escape(date_gr(g['date']))} · {escape(summary(g['docs']))}</title>"
            f"<link>{link}</link>"
            f'<guid isPermaLink="true">{link}</guid>'
            f"<pubDate>{format_datetime(datetime(y, m, d, tzinfo=UTC))}</pubDate>"
            f"<description>{escape(''.join(html))}</description>"
            "</item>"
        )
    built = format_datetime(datetime.fromisoformat(built_at).astimezone(UTC))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n<channel>\n'
        "<title>farmaka.info · Τι άλλαξε</title>\n"
        f"<link>{page}</link>\n"
        f'<atom:link href="{page}rss.xml" rel="self" type="application/rss+xml"/>\n'
        "<description>Αλλαγές τιμών, νέες συσκευασίες και λίστες ελλείψεων φαρμάκων, από τα δελτία του "
        "Υπουργείου Υγείας και τον ΕΟΦ.</description>\n"
        "<language>el</language>\n"
        f"<lastBuildDate>{built}</lastBuildDate>\n" + "\n".join(items) + "\n</channel>\n</rss>\n"
    )
