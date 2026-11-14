"""Price history across several full lists (ingest.build) and the backfill crawl
(ingest.fetch --backfill). No network: older lists are written with openpyxl,
the ministry site is a stand-in built from the sample listing manifest."""

import html
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import openpyxl
import pytest
import requests

from ingest import build, fetch, moh
from ingest.moh import Article, classify, read_manifest
from ingest.normalize import read_bulletin

RAW = build.ROOT / "samples" / "raw"
HEADER = [
    "Κωδικός",
    "Barcode",
    "Προϊόν",
    "ATC",
    "Μη Αποζημιούμενο",
    "Τιμή Παραγωγού",
    "Χονδρική Τιμή",
    "Λιανική Τιμή",
    "Δραστική ουσία",
    "Κάτοχος Άδειας Κυκλοφορίας",
    "ΦΠΑ",
]
XYDALBA, DORALIN, MAXUDIN = "312080101", "210040201", "202420102"
GONE = "999999905"  # in the older list only


def ean(code: str) -> str:
    b = "280" + code
    return b + str((10 - sum(int(x) * (3 if i % 2 else 1) for i, x in enumerate(b)) % 10) % 10)


def cells(code: str, retail, name: str = "TEST TAB 10MG/TAB BTx30") -> list:
    """One spreadsheet row; `retail` may be a text cell to test ambiguous values."""
    r = retail if isinstance(retail, float) else 10.0
    return [
        code,
        ean(code),
        name,
        "A01AA01",
        " ",
        round(r * 0.7, 2),
        round(r * 0.73, 2),
        retail,
        "TESTINE",
        "TEST MAH",
        0.06,
    ]


def from_row(r, retail: float | None = None) -> list:
    """A row of the sample full list, optionally at another retail price."""
    ex, wh, rt = (r.price_exfactory, r.price_wholesale, r.price_retail)
    if retail is not None:
        ex, wh, rt = round(retail * 0.8, 2), round(retail * 0.85, 2), retail
    nr = "N" if r.non_reimbursed else " "
    return [r.eof_code, r.barcode, r.name, r.atc, nr, ex, wh, rt, ":".join(r.substances), r.mah, r.vat]


def xlsx(path: Path, rows: list[list]) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(HEADER)
    for r in rows:
        ws.append(r)
    wb.save(path)
    return path


def assert_chained(history: list[dict]) -> None:
    """Per (eof_code, field) every event starts where the previous one ended."""
    prev: dict[tuple[str, str], object] = {}
    for h in history:
        k = (h["eof_code"], h["field"])
        assert h["old"] == prev.get(k), h
        assert h["change"] in build.CHANGES
        prev[k] = h["new"]


SAMPLES = [(d, i, k, RAW / f) for d, i, k, f in build.SAMPLES]


@pytest.fixture(scope="module")
def sample_rows():
    return {r.eof_code: r for r in read_bulletin(RAW / "moh_14818_epikairopoihsh_2026-09.xlsx")}


def run_build(sources, out: Path):
    issues, provenance, report = [], {}, {}
    base, deltas, rows, history = build.merge(sources, issues, provenance, report)
    build.write(out, base, deltas, rows, history, build.validate(rows), issues, provenance, {}, [], report)
    return base, deltas, rows, history, report


def test_older_full_list_adds_history_not_current_state(tmp_path, sample_rows):
    assert GONE not in sample_rows
    older = xlsx(
        tmp_path / "older_full.xlsx",
        [
            from_row(sample_rows[XYDALBA], retail=900.0),
            from_row(sample_rows[DORALIN]),
            from_row(sample_rows[MAXUDIN], retail=3.5),
            cells(GONE, 12.0, name="GONE TAB 5MG/TAB BTx20"),
        ],
    )
    between = xlsx(tmp_path / "delta_between.xlsx", [from_row(sample_rows[DORALIN], retail=9.0)])
    too_old = xlsx(tmp_path / "delta_too_old.xlsx", [from_row(sample_rows[DORALIN], retail=7.0)])
    extra = [
        ("2026-05-01", 13900, "delta", too_old),
        ("2026-06-01", 14000, "full", older),
        ("2026-07-01", 14100, "delta", between),
    ]
    base1, deltas1, rows1, hist1, _ = run_build(SAMPLES, tmp_path / "samples")
    base2, deltas2, rows2, hist2, report = run_build(extra + SAMPLES, tmp_path / "history")

    # the current state is still the newest full list + the deltas after it
    assert (base2, deltas2) == (base1, deltas1)
    assert rows2.keys() == rows1.keys() and len(rows2) == 8922
    for name in ("packages.csv", "packages.json"):
        assert (tmp_path / "history" / name).read_bytes() == (tmp_path / "samples" / name).read_bytes()
    assert GONE not in rows2

    assert_chained(hist1)
    assert_chained(hist2)

    def events(code):
        return [
            (h["change"], h["old"], h["new"], h["moh_article"])
            for h in hist2
            if h["eof_code"] == code and h["field"] == "price_retail"
        ]

    assert events(XYDALBA) == [
        ("first_seen", None, 900.0, 14000),
        ("update", 900.0, 823.64, 14818),  # the newer full list differs from the state
        ("update", 823.64, 547.94, 14930),
    ]
    assert events(DORALIN) == [
        ("first_seen", None, 8.19, 14000),
        ("update", 8.19, 9.0, 14100),  # delta between the two full lists
        ("update", 9.0, 8.19, 14818),
    ]
    assert events(MAXUDIN) == [("first_seen", None, 3.5, 14000), ("update", 3.5, 4.07, 14818)]
    assert events(GONE) == [("first_seen", None, 12.0, 14000), ("withdrawn", 12.0, None, 14818)]
    assert {h["field"] for h in hist2 if h["change"] == "withdrawn"} == set(build.PRICE_FIELDS)
    # codes only in the newer full list start there
    assert events("340950101") == [("new", None, 10.98, 14872)]
    seen_at_14818 = {
        h["eof_code"] for h in hist2 if h["change"] == "first_seen" and h["moh_article"] == 14818
    }
    assert len(seen_at_14818) == 8917 - 3
    assert 13900 not in {h["moh_article"] for h in hist2}  # older than every full list

    assert report["withdrawn"] == [
        {
            "eof_code": GONE,
            "name": "GONE TAB 5MG/TAB BTx20",
            "price_retail": 12.0,
            "last_listed": {"published": "2026-06-01", "moh_article": 14000},
        }
    ]
    rep = json.loads((tmp_path / "history" / "build_report.json").read_text(encoding="utf-8"))
    assert rep["baseline"]["moh_article"] == 14818
    assert [d["moh_article"] for d in rep["deltas_applied"]] == [14872, 14930]
    assert rep["withdrawn_since_last_full"] == report["withdrawn"]
    assert [d["moh_article"] for d in rep["history"]["fulls_applied"]] == [14000, 14818]
    assert [d["moh_article"] for d in rep["history"]["older_deltas_applied"]] == [14100]
    assert [d["moh_article"] for d in rep["history"]["deltas_before_first_full"]] == [13900]
    assert rep["history"]["events"]["withdrawn"] == 4
    srcs = json.loads((tmp_path / "history" / "sources.json").read_text(encoding="utf-8"))
    assert [s["moh_article"] for s in srcs] == [14000, 14100, 14818, 14872, 14930]


def test_withdrawn_reappeared_and_unreadable_rows(tmp_path):
    a, b, c, e = "111111101", "111111102", "111111103", "111111104"
    sources = [
        (
            "2026-01-10",
            100,
            "full",
            xlsx(tmp_path / "f1.xlsx", [cells(a, 10.0), cells(b, 20.0), cells(c, 30.0), cells(e, 40.0)]),
        ),
        # b and e are missing; c is listed but its price cell is ambiguous
        ("2026-02-10", 200, "full", xlsx(tmp_path / "f2.xlsx", [cells(a, 10.0), cells(c, "17,07\n\n19,40")])),
        ("2026-02-20", 250, "delta", xlsx(tmp_path / "d.xlsx", [cells(b, 21.0)])),
        (
            "2026-03-10",
            300,
            "full",
            xlsx(tmp_path / "f3.xlsx", [cells(a, 11.0), cells(b, 21.0), cells(c, 31.0), cells(e, 40.0)]),
        ),
    ]
    issues, provenance, report = [], {}, {}
    base, deltas, rows, history = build.merge(sources, issues, provenance, report)
    assert base[1] == 300 and deltas == []
    assert sorted(rows) == [a, b, c, e]
    assert provenance[b] == {"published": "2026-03-10", "moh_article": 300}
    assert [i["eof_code"] for i in issues] == [c]
    assert report["withdrawn"] == []  # nothing missing from the newest list
    assert_chained(history)

    def events(code):
        return [
            (h["change"], h["moh_article"])
            for h in history
            if h["eof_code"] == code and h["field"] == "price_retail"
        ]

    assert events(a) == [("first_seen", 100), ("update", 300)]
    assert events(b) == [("first_seen", 100), ("withdrawn", 200), ("reappeared", 250)]  # back by a delta
    assert events(c) == [("first_seen", 100), ("update", 300)]  # unreadable at 200: no withdrawal
    assert events(e) == [("first_seen", 100), ("withdrawn", 200), ("reappeared", 300)]  # back by a full list
    # deterministic
    assert build.merge(sources, [], {}, {})[3] == history


def test_full_list_code_must_have_nine_digits(tmp_path):
    bad = cells("111111101", 10.0)
    bad[0] = "90201"  # a numeric cell that lost its leading zeros
    with pytest.raises(ValueError, match="not 9 digits"):
        build.merge([("2026-01-10", 100, "full", xlsx(tmp_path / "f.xlsx", [bad]))])


# --- backfill crawl -------------------------------------------------------

LISTING = json.loads(build.SAMPLES_MANIFEST.read_text(encoding="utf-8"))  # newest first
FULLS = [x for x in LISTING if classify(x["title"]) == "full"]


class FakeSite:
    """Stand-in for moh.gov.gr: 20 articles per listing page, newest first;
    past the last page it serves the last page again."""

    per_page = 20

    def __init__(self):
        self.requests: list[str] = []
        self.fail: set[str] = set()

    def get(self, url: str):
        self.requests.append(url)
        if url in self.fail:
            raise requests.ConnectionError(f"connection reset: {url}")
        if url.startswith(moh.PRICE_BULLETINS + "?page="):
            last = -(-len(LISTING) // self.per_page)
            page = min(int(url.rsplit("=", 1)[1]), last)
            text = "".join(
                f'<div class="item"><span class="datetime">{x["date"]}</span>'
                f'<h3><a href="{x["url"]}">{html.escape(x["title"], quote=False)}</a></h3></div>'
                for x in LISTING[(page - 1) * self.per_page : page * self.per_page]
            )
            return SimpleNamespace(text=text, content=text.encode())
        if "?fdl=" in url:
            return SimpleNamespace(text="", content=b"xlsx:" + url.encode())
        aid = url.rsplit("/", 1)[1].split("-", 1)[0]
        text = (
            f'<a href="{url}?fdl={aid}1">pinakas.xlsx Μέγεθος: 1.2 MB</a>'
            f'<a href="{url}?fdl={aid}2">YA.pdf Μέγεθος: 300 KB</a>'
        )
        return SimpleNamespace(text=text, content=text.encode())


@pytest.fixture
def site(monkeypatch):
    s = FakeSite()
    monkeypatch.setattr(moh.Client, "get", lambda self, url: s.get(url))
    return s


def crawl(monkeypatch, out: Path, *args: str) -> None:
    monkeypatch.setattr(sys, "argv", ["ingest.fetch", "--section", "prices", "--out", str(out), *args])
    fetch.main()


def xlsx_of(x: dict) -> str:
    return f"{x['url']}?fdl={x['article_id']}1"


def test_backfill_downloads_only_full_lists(tmp_path, site, monkeypatch):
    assert len(LISTING) == 343 and len(FULLS) == 16
    crawl(monkeypatch, tmp_path, "--backfill", "full")
    listing = [u for u in site.requests if "?page=" in u]
    assert listing == [
        f"{moh.PRICE_BULLETINS}?page={n}" for n in range(1, 20)
    ]  # 18 pages + 1 with nothing new
    assert sorted(u for u in site.requests if "?" not in u) == sorted(x["url"] for x in FULLS)
    assert sorted(p.name for p in (tmp_path / "raw").iterdir()) == sorted(
        f"moh_{x['article_id']}_{x['article_id']}1.xlsx" for x in FULLS
    )
    saved = read_manifest(tmp_path / "manifest_prices.json")
    assert len(saved) == 343
    assert sum(1 for x in saved if x.kind == "full" and x.checked and x.attachments) == 16

    site.requests.clear()  # second run: nothing left but reading the listing
    crawl(monkeypatch, tmp_path, "--backfill", "full")
    assert all("?page=" in u for u in site.requests)


def test_backfill_resumes_after_a_failure(tmp_path, site, monkeypatch):
    broken = next(x for x in FULLS if x["article_id"] == 13863)
    site.fail = {xlsx_of(broken)}
    with pytest.raises(SystemExit) as stop:
        crawl(monkeypatch, tmp_path, "--backfill", "full")
    assert stop.value.code == 1
    assert len(list((tmp_path / "raw").iterdir())) == 15

    site.fail.clear()
    site.requests.clear()
    crawl(monkeypatch, tmp_path, "--backfill", "full")
    assert [u for u in site.requests if "?page=" not in u] == [xlsx_of(broken)]
    assert len(list((tmp_path / "raw").iterdir())) == 16


def test_daily_run_after_backfill_stays_on_recent_articles(tmp_path, site, monkeypatch):
    crawl(monkeypatch, tmp_path, "--backfill", "full")
    site.requests.clear()
    crawl(monkeypatch, tmp_path, "--max-pages", "1")
    pages = {u for u in site.requests if "?" not in u}
    assert pages and pages <= {x["url"] for x in LISTING[:20]}  # not the other ~290 in the manifest


def test_daily_scope_keeps_everything_from_the_newest_full_list():
    def art(aid, date, kind):
        return Article(aid, date, "t", f"u/{aid}", kind)

    arts = [art(4, "01/04/2026", "delta"), art(3, "01/03/2026", "full"), art(2, "01/02/2026", "delta")]
    assert [x.article_id for x in fetch.daily_scope(arts, [])] == [4, 3]
    assert [x.article_id for x in fetch.daily_scope(arts, [arts[2]])] == [4, 3, 2]
    assert fetch.daily_scope(arts[2:], []) == arts[2:]  # no full list known yet: everything
