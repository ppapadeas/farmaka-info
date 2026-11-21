import json
import sqlite3
from pathlib import Path

import pytest

from ingest import build as ingest_build
from sitegen.build import build, shard_of
from sitegen.text import date_gr, eur, skeleton, slugify

HERE = Path(__file__).resolve().parent
VECTORS = json.loads((HERE / "skeleton_vectors.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("text,expected", VECTORS["skeleton"])
def test_skeleton_vectors(text, expected):
    # the same vectors run against web/static/search-core.js (web/test/search.test.mjs)
    assert skeleton(text) == expected


@pytest.mark.parametrize("query,text", VECTORS["matches"])
def test_greek_queries_match_latin_names(query, text):
    hay = skeleton(text)
    tokens = [t[:-1] if len(t) >= 5 and t[-1] in "aiou" else t for t in skeleton(query).split()]
    assert all(t in hay for t in tokens)


def test_formatting():
    assert eur(1234.5) == "1.234,50 €"
    assert eur(0.22) == "0,22 €"
    assert date_gr("2026-10-05") == "05/10/2026"
    assert slugify("METFORMIN HYDROCHLORIDE") == "metformin-hydrochloride"
    assert slugify("ΣΙΔΗΡΟΣ") == "sidiros"


def test_shard_is_stable():
    # web/worker.js::shardOf must give the same values
    assert shard_of("312080101") == "5e"
    assert shard_of("000090201") == "3c"


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    data = tmp_path_factory.mktemp("data")
    out = tmp_path_factory.mktemp("dist")
    sources = [(d, i, k, ingest_build.ROOT / "samples/raw" / f) for d, i, k, f in ingest_build.SAMPLES]
    articles = ingest_build.article_index(
        json.loads(ingest_build.SAMPLES_MANIFEST.read_text(encoding="utf-8"))
    )
    issues, provenance = [], {}
    base, deltas, rows, history = ingest_build.merge(sources, issues, provenance)
    pending = ingest_build.pending_bulletins(sources, base, articles)
    ingest_build.write(
        data, base, deltas, rows, history, ingest_build.validate(rows), issues, provenance, articles, pending
    )
    info = build(data, out, "2026-11-14T00:00:00+00:00")
    return out, info


def test_site_has_every_package_page(site):
    out, info = site
    assert info["packages"] == 8922
    assert len(list((out / "farmako").iterdir())) == 8922
    assert info["files"] < 20000  # Workers static assets limit on the free plan


def test_package_page_shows_price_and_source(site):
    out, _ = site
    html = (out / "farmako" / "312080101" / "index.html").read_text(encoding="utf-8")
    assert "547,94 €" in html  # XYDALBA after the price cut
    assert "Ήταν 823,64 € έως 06/11/2026" in html
    assert "14930-symplhrwmatiko" in html  # link to the bulletin that set the price
    assert "δεν αντικαθιστά γιατρό ή φαρμακοποιό" in html
    assert 'href="/sxetika/#ti-deixnei"' in html  # a price is not proof it is on the market
    assert 'class="spark"' in html
    assert "<script>" not in html  # CSP: no inline scripts
    about = (out / "sxetika" / "index.html").read_text(encoding="utf-8")
    assert 'id="ti-deixnei"' in about and "όχι τα ράφια των φαρμακείων" in about


def test_otc_price_is_only_a_recommendation():
    from sitegen import pages

    card = pages.price_card({"kind": "otc", "prices": {"retail": 3.1}}, "6%", "")
    assert "Ενδεικτική τιμή λιανικής" in card and "το φαρμακείο ορίζει τη δική του" in card
    assert "φθηνότερα" not in card  # the pharmacy sets its own price, either way


def test_api_shard_and_search_index(site):
    out, _ = site
    shard = json.loads((out / "api" / "pk" / f"{shard_of('312080101')}.json").read_text(encoding="utf-8"))
    p = shard["312080101"]
    assert p["prices"]["retail"] == 547.94
    assert p["price_source"]["moh_article"] == 14930
    updates = [h for h in p["price_history"] if h["field"] == "price_retail" and h["change"] == "update"]
    assert [(h["old"], h["new"]) for h in updates] == [(823.64, 547.94)]
    lines = (out / "search-index.tsv").read_text(encoding="utf-8").splitlines()
    assert len([x for x in lines if not x.startswith("#")]) == 8922
    assert all(len(x.split("\t")) == 11 for x in lines[1:])


def test_greek_substance_names_and_substance_index(site):
    out, _ = site
    html = (out / "ousia" / "atorvastatin" / "index.html").read_text(encoding="utf-8")
    assert "<h1>Ατορβαστατίνη</h1>" in html and '<p class="inn">Atorvastatin' in html
    lines = (out / "substance-index.tsv").read_text(encoding="utf-8").splitlines()
    row = next(x.split("	") for x in lines if x.startswith("atorvastatin	"))
    assert row[1:3] == ["ATORVASTATIN", "Ατορβαστατίνη"]
    assert (out / "ousies" / "index.html").exists()


def test_module_imports_carry_the_build_version(site):
    # a new app.js must never run against an old cached search-core.js
    out, _ = site
    from sitegen.pages import ASSET_VERSION

    app = (out / "static" / "app.js").read_text(encoding="utf-8")
    assert f'"./search-core.js?v={ASSET_VERSION}"' in app
    assert '"./search-core.js"' not in app


def test_unit_counts_and_strengths():
    from sitegen.drug import form_group, strength, unit_count

    assert unit_count("SEVELAMER/FARAN F.C.TAB 800MG/TAB BTx 2 BOTTLE (HDPE) x 180 TABS") == 360
    assert unit_count("ZYTIGA F.C.TAB 500MG/TAB BTx5 BLISTERS θήκες x12 δισκία (60 δισκία)") == 60
    assert unit_count("AVOMIN F.C.TAB 1MG/TAB BTx2 BL x14 TABS") == 28
    assert unit_count("ZARASTIN F.C.TAB 20MG/TAB BTx14 BLIST (2x7)") == 14  # blister layout, not 2 x 7 packs
    assert unit_count("ATORVAL F.C.TAB 20MG/TAB BTx30 (BLIST 3x10)") == 30
    assert (
        strength("UVICALDE CHW.TAB (600MG+2000 IU)/TAB BT X 30")[0]
        != strength("CAL+D CHW.TAB (600MG+400 IU)/TAB BTx60")[0]
    )
    assert strength("COVERAM TABLET (10+5)MG/TAB BT x 30")[0] == "10+5MG"
    # Directive 2001/83/EC art. 10: immediate- and modified-release forms never compare
    assert form_group("GLUCOPHAGE PR.TAB 500MG/TAB BTx30") == "oral-mr"
    assert form_group("GLUCOPHAGE F.C.TAB 500MG/TAB BTx30") == "oral-ir"


def test_trivia_page(site):
    out, _ = site
    html = (out / "perierga" / "index.html").read_text(encoding="utf-8")
    assert html.count('class="fact"') >= 10
    assert "Η ακριβότερη συσκευασία" in html and "nan" not in html


def test_meta_lists_pending_bulletins(site):
    out, _ = site
    meta = json.loads((out / "api" / "meta.json").read_text(encoding="utf-8"))
    assert [p["moh_article"] for p in meta["pending_bulletins"]] == [14828, 14870, 14871]
    assert meta["license"]["data"] == "CC0-1.0"


def test_sqlite_dump(site):
    out, _ = site
    con = sqlite3.connect(out / "data" / "farmaka.sqlite")
    assert con.execute("select count(*) from package").fetchone()[0] == 8922
    assert con.execute("select price_retail from package where eof_code='312080101'").fetchone()[0] == 547.94
    con.close()


def test_shortage_alert(tmp_path):
    import csv

    from sitegen.build import load_shortages

    path = tmp_path / "shortages.csv"
    cols = [
        "eof_code",
        "list_type",
        "status",
        "published",
        "valid_until",
        "start",
        "alternatives",
        "source_url",
        "source_title",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerow(
            {
                "eof_code": "1",
                "list_type": "limited_availability",
                "status": "Καθυστέρηση παράδοσης",
                "published": "2026-10-31",
                "valid_until": "2026-12-31",
            }
        )
        w.writerow(
            {
                "eof_code": "2",
                "list_type": "limited_availability",
                "published": "2026-05-31",
                "valid_until": "2026-06-30",
            }
        )  # expired: not shown
        w.writerow(
            {
                "eof_code": "3",
                "list_type": "discontinuation",
                "published": "2025-12-31",
                "valid_until": "2026-01-31",
            }
        )  # a discontinuation stays
        w.writerow({"eof_code": "", "list_type": "eu_shortage"})  # unmatched: not attached to any package
    got = load_shortages(tmp_path, "2026-11-14")
    assert set(got) == {"1", "3"}
    assert got["1"][0]["reason"] == "Καθυστέρηση παράδοσης"


LA = {"type": "limited_availability", "published": "2026-10-31", "source_url": "https://www.eof.gr/x/"}
STOP = {"type": "discontinuation", "published": "2025-12-31", "source_url": "https://www.eof.gr/y/"}


def _pkg(code, brand, retail, n=30, *, rp=None, availability=(), strength="20MG"):
    from sitegen.build import REIMBURSEMENT_SOURCE

    reimb = []
    if rp is not None:
        reimb = [
            {
                "list": "main",
                "reimbursement_price": rp,
                "retail_in_list": retail,
                "daily_cost": None,
                "daily_doses_per_pack": None,
                "dispensing": None,
                "restrictions": [],
                "source": REIMBURSEMENT_SOURCE,
            }
        ]
    return {
        "eof_code": code,
        "barcode": f"280{code}0",
        "name": f"{brand} F.C.TAB {strength}/TAB BTx{n}",
        "atc": "C10AA05",
        "substances": ["ATORVASTATIN"],
        "mah": f"{brand} ΑΕ",
        "vat": 0.06,
        "non_reimbursed": False,
        "kind": "regulated",
        "prices": {"exfactory": None, "wholesale": None, "retail": retail},
        "price_source": {"published": "2026-10-05", "moh_article": 14818, "url": "https://www.moh.gov.gr/a"},
        "price_history": [],
        "reimbursement": reimb,
        "availability": [dict(a) for a in availability],
    }


def _ctx(pkgs):
    from sitegen import pages
    from sitegen.build import REIMBURSEMENT_SOURCE, equivalents, substance_index

    subst = substance_index(pkgs)
    meta = {
        "built_at": "2026-11-15T00:00:00+00:00",
        "reimbursement_source": REIMBURSEMENT_SOURCE,
        "shortages": {"limited_availability": {"packages": 1, "published": "2026-10-31"}},
    }
    return pages.Context(
        meta=meta,
        by_code={p["eof_code"]: p for p in pkgs},
        subst=subst,
        slug_of={v["name"]: k for k, v in subst.items()},
        downloads=[],
        equiv=equivalents(pkgs),
    )


def test_shortage_points_to_available_equivalents():
    from sitegen import pages

    p = _pkg("100000001", "SHORTY", 10.0, rp=8.0, availability=[LA])
    pkgs = [
        p,
        _pkg("100000002", "ALSOSHORT", 6.0, rp=6.0, availability=[LA]),  # cheapest, but in shortage too
        _pkg("100000003", "BETA", 7.0, rp=8.0),
        _pkg("100000004", "GAMMA", 9.0, rp=8.0),
        _pkg("100000005", "DELTA", 5.0, rp=5.0, availability=[STOP]),
        _pkg("100000006", "EPSILON", 8.0),  # not reimbursed
        _pkg("100000007", "OTHER", 4.0, strength="40MG"),  # another strength: no group
    ]
    ctx = _ctx(pkgs)

    card = pages.equivalents_card(ctx, p)
    order = [card.index(f">{b}<") for b in ("BETA", "EPSILON", "GAMMA", "DELTA", "ALSOSHORT", "SHORTY")]
    assert order == sorted(order)  # the three on no EOF list first, cheapest per unit first
    assert "3 ισοδύναμα δεν είναι σε λίστα ελλείψεων ή διακοπής του ΕΟΦ" in card
    assert "χωρίς διαφορά" in card  # BETA: reimbursement price above retail
    assert "διαφορά 1,00 €" in card  # GAMMA: 9 € retail, 8 € reimbursement
    assert "διαφορά 2,00 €" in card  # this one
    assert "χωρίς αποζημίωση" in card  # EPSILON
    assert 'class="tag stop"' in card and 'class="tag warn"' in card
    assert "επιβεβαιώνεται στο φαρμακείο" in card

    alert = pages.availability_card(ctx, p)
    assert '<a href="#isodynama">3 ισοδύναμα χωρίς ένδειξη έλλειψης ↓</a>' in alert
    assert "όχι το απόθεμα των φαρμακείων" in alert  # the lists are not the pharmacy shelves

    page = pages.shortages_page(ctx)
    assert '<a class="eqn" href="/farmako/100000001/#isodynama">3 ισοδύναμα χωρίς έλλειψη</a>' in page
    assert "όχι το απόθεμα των φαρμακείων" in page

    # the same group seen from a package that is not in shortage: cheapest first, as before
    beta = pages.equivalents_card(ctx, ctx.by_code["100000003"])
    assert beta.index(">DELTA<") < beta.index(">ALSOSHORT<") < beta.index(">BETA<")
    assert "διαφορά" not in beta


def test_shortage_without_available_equivalents_says_so():
    from sitegen import pages

    p = _pkg("200000001", "SHORTY", 10.0, availability=[LA])
    q = _pkg("200000002", "ALSOSHORT", 9.0, availability=[STOP])
    alone = _pkg("200000003", "ALONE", 9.0, availability=[LA], strength="80MG")
    ctx = _ctx([p, q, alone])

    assert "Όλα τα ισοδύναμα είναι κι αυτά σε λίστα" in pages.equivalents_card(ctx, p)
    assert "Κανένα ισοδύναμο χωρίς ένδειξη έλλειψης" in pages.availability_card(ctx, p)
    alert = pages.availability_card(ctx, alone)
    assert "Δεν βρέθηκε ισοδύναμο με ίδια ουσία, περιεκτικότητα και μορφή" in alert
    assert "#isodynama" not in alert and pages.equivalents_card(ctx, alone) == ""
    page = pages.shortages_page(ctx)
    assert 'href="/farmako/200000001/#isodynama">κανένα ισοδύναμο χωρίς έλλειψη</a>' in page
    assert '<span class="eqn none">κανένα ισοδύναμο</span>' in page


def test_patient_cost():
    from sitegen.pages import patient_cost

    # retail 10, reimbursement price 8: participation on 8, plus the 2 € difference
    assert [patient_cost(10.0, 8.0, r) for r in (0, 10, 25)] == [2.0, 2.8, 4.0]
    # reimbursement price above retail: participation on the retail price, no difference
    assert [patient_cost(5.0, 8.0, r) for r in (0, 10, 25)] == [0.0, 0.5, 1.25]
    assert patient_cost(12.35, 12.35, 10) == 1.24  # 1.235: half up, not to even
    assert patient_cost(7.77, 7.0, 25) == 2.52  # 1.75 + 0.77


def test_what_will_i_pay():
    from sitegen import pages

    p = _pkg("300000001", "DEAR", 10.0, rp=8.0)
    pkgs = [
        p,
        _pkg("300000002", "CHEAP", 8.5, rp=8.0),  # cheaper at every rate
        _pkg("300000003", "CHEAPEST", 6.0, rp=6.0, availability=[LA]),  # in shortage: not offered
        _pkg("300000004", "NOREIMB", 4.0),  # not reimbursed: not offered
    ]
    ctx = _ctx(pkgs)
    card = pages.reimbursement_card(ctx, p)
    assert "Πόσο θα πληρώσω;" in card and "<table" in card
    rows = [r.split("</tr>")[0] for r in card.split('<tr><th scope="row">')[1:]]
    assert rows == [
        "0%</th><td>2,00 €</td><td>0,50 €</td>",
        "10%</th><td>2,80 €</td><td>1,30 €</td>",
        "25%</th><td>4,00 €</td><td>2,50 €</td>",
    ]
    assert '<a href="/farmako/300000002/">CHEAP</a>' in card
    assert "το τελικό ποσό το λέει ο φαρμακοποιός" in card and "Ενδεικτικά" in card
    assert "/issues/new?title=" in card and "Πείτε μας" in card  # a way to report a different amount
    assert "κατάλογο 22/10/2026" in card and "δελτίο τιμών 05/10/2026" in card
    assert "διαφορετικό μέγεθος" not in card.lower()

    # the cheapest is the package itself: only its own column
    cheap = pages.reimbursement_card(ctx, ctx.by_code["300000002"])
    assert cheap.count('<th scope="col">') == 2 and "<td>0,50 €</td></tr>" in cheap

    # a bigger box with a lower retail price per unit that leaves a difference to pay: not "cheaper"
    p2 = _pkg("400000001", "DEAR", 10.0, n=30, rp=10.0)
    big = _pkg("400000002", "BIGBOX", 18.0, n=60, rp=16.0)  # 0%: 2 € for 60 against 0 € for 30
    assert pages.cheaper_reimbursed(_ctx([p2, big]), p2) is None
    # per unit cheaper at every rate: offered, with a note on the pack sizes
    big = _pkg("400000002", "BIGBOX", 18.0, n=60, rp=18.0)
    card = pages.reimbursement_card(_ctx([p2, big]), p2)
    assert "BIGBOX" in card and "Διαφορετικό μέγεθος συσκευασίας" in card and "×60" in card


def test_changes_collect(tmp_path):
    import csv
    import xml.etree.ElementTree as ET

    from sitegen import changes

    sources = [
        {
            "moh_article": 1,
            "published": "2026-01-10",
            "kind": "full",
            "title": "Πρώτος κατάλογος",
            "url": "https://m/1",
        },
        {
            "moh_article": 2,
            "published": "2026-08-01",
            "kind": "delta",
            "title": "Παλιό δελτίο",
            "url": "https://m/2",
        },
        {
            "moh_article": 3,
            "published": "2026-10-01",
            "kind": "full",
            "title": "Νέος κατάλογος",
            "url": "https://m/3",
        },
        {
            "moh_article": 4,
            "published": "2026-10-20",
            "kind": "delta",
            "title": "Δελτίο",
            "url": "https://m/4",
        },
    ]

    def ev(code, change, old, new, art, field="price_retail"):
        pub = next(s["published"] for s in sources if s["moh_article"] == art)
        return {
            "eof_code": code,
            "field": field,
            "old": old,
            "new": new,
            "change": change,
            "published": pub,
            "moh_article": str(art),
        }

    history = [
        ev("A", "first_seen", "", "10.0", 1),  # the baseline: not a change
        ev("A", "update", "10.0", "12.0", 2),  # before the window
        ev("A", "update", "12.0", "9.0", 3),
        ev("A", "update", "5.1", "5.0", 3, field="price_exfactory"),  # not the retail price
        ev("B", "withdrawn", "7.5", "", 3),
        ev("C", "first_seen", "", "4.0", 3),  # first seen in a later full list: new
        ev("D", "new", "", "3.0", 4),
        ev("E", "reappeared", "", "6.0", 4),
        ev("A", "update", "9.0", "9.9", 4),
    ]
    lists = tmp_path / "shortages.csv"
    with lists.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(
            fh, fieldnames=["eof_code", "list_type", "published", "name", "source_url", "source_title"]
        )
        w.writeheader()
        for code, t in (("A", "limited_availability"), ("", "limited_availability"), ("C", "eu_shortage")):
            w.writerow(
                {"eof_code": code, "list_type": t, "published": "2026-10-31", "name": f"LIST {code or 'X'}"}
                | {"source_url": "https://e/la", "source_title": "Λίστα 31/10"}
            )
    names = {
        "A": "ALPHA TABLET 1MG/TAB BTx30",
        "B": "BETA TABLET 1MG/TAB BTx30",
        "C": "GAMMA",
        "D": "DELTA",
        "E": "EPS",
    }
    pending = [
        {
            "moh_article": 5,
            "published": "2026-10-25",
            "title": "Σε αναμονή",
            "url": "https://m/5",
            "kind": "delta",
        }
    ]
    ch = changes.collect(history, sources, names, lists, pending)

    assert (ch["start"], ch["end"]) == ("2026-09-01", "2026-10-31")
    assert [g["date"] for g in ch["days"]] == ["2026-10-31", "2026-10-25", "2026-10-20", "2026-10-01"]
    la, wait, d4, d3 = (g["docs"] for g in ch["days"])
    assert [(it["eof_code"], it["name"]) for it in la[0]["items"]] == [
        ("A", names["A"]),
        ("", "LIST X"),
    ]  # no EMA
    assert wait[0]["pending"] and not wait[0]["items"]
    assert [(it["eof_code"], changes.category(it)) for it in d3[0]["items"]] == [
        ("A", "cut"),
        ("C", "new"),
        ("B", "withdrawn"),
    ]
    assert changes.summary(d3) == "1 μείωση τιμής, 1 νέα συσκευασία, 1 βγήκε από το δελτίο"
    assert changes.summary(d4) == "1 αύξηση τιμής, 1 νέα συσκευασία, 1 ξανά στο δελτίο"

    out = tmp_path / "allages.csv"
    assert changes.write_csv(ch, out) == 8
    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert rows[-1] == {
        "published": "2026-10-01",
        "publisher": "moh",
        "source_id": "3",
        "source_title": "Νέος κατάλογος",
        "source_url": "https://m/3",
        "change": "withdrawn",
        "eof_code": "B",
        "name": names["B"],
        "old": "7.50",
        "new": "",
        "pct": "",
    }
    assert next(r for r in rows if r["change"] == "price" and r["source_id"] == "3")["pct"] == "-25.0"

    rss = ET.fromstring(changes.rss(ch, "2026-11-15T06:00:00+00:00", "https://farmaka.info"))
    items = rss.findall("./channel/item")
    assert [i.findtext("link") for i in items][0] == "https://farmaka.info/allages/#d-2026-10-31"
    assert (
        items[3].findtext("title") == "01/10/2026 · 1 μείωση τιμής, 1 νέα συσκευασία, 1 βγήκε από το δελτίο"
    )
    assert "ALPHA TABLET 1MG/TAB BTx30: 12,00 € → 9,00 € (−25,0%)" in items[3].findtext("description")
    assert items[3].findtext("pubDate") == "Thu, 01 Oct 2026 00:00:00 +0000"
    assert changes.pct_label(4.34, 4.35) == "+0,2%"  # a small rise never reads "+0%"


def test_changes_page_feed_and_csv(site):
    import csv
    import xml.etree.ElementTree as ET

    out, info = site
    html = (out / "allages" / "index.html").read_text(encoding="utf-8")
    assert html.index('id="d-2026-11-06"') < html.index('id="d-2026-10-21"') < html.index('id="d-2026-10-05"')
    assert "14930-symplhrwmatiko" in html  # XYDALBA's price cut, under the bulletin that made it
    assert (
        '<span class="old">823,64 €</span><span class="new">547,94 €</span><span class="pill down">−33,5%</span>'
        in html
    )
    assert "Ο πρώτος κατάλογος που έχουμε" in html  # 14818 is the baseline of the samples
    assert "Σε αναμονή: δεν έχει ενσωματωθεί" in html  # 14870, 14871 are listed but not applied
    assert "<script>" not in html

    feed = ET.fromstring((out / "allages" / "rss.xml").read_text(encoding="utf-8"))
    links = [i.findtext("link") for i in feed.findall("./channel/item")]
    assert links[0] == "https://farmaka.info/allages/#d-2026-11-06"
    assert all(x.startswith("https://farmaka.info/allages/#d-") for x in links)

    with (out / "data" / "allages.csv").open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    x = next(r for r in rows if r["eof_code"] == "312080101")
    assert (x["change"], x["old"], x["new"], x["pct"], x["source_id"]) == (
        "price",
        "823.64",
        "547.94",
        "-33.5",
        "14930",
    )
    assert not any(r["source_id"] == "14818" for r in rows)  # first_seen of the baseline are not changes
    assert "new" in {r["change"] for r in rows}  # the generics of 14872

    home = (out / "index.html").read_text(encoding="utf-8")
    assert (
        '<link rel="alternate" type="application/rss+xml" title="farmaka.info · Τι άλλαξε" href="/allages/rss.xml">'
        in home
    )
    assert '<a href="/allages/">Τι άλλαξε</a>' in home
    data = (out / "dedomena" / "index.html").read_text(encoding="utf-8")
    assert 'href="/data/allages.csv"' in data and 'href="/allages/rss.xml"' in data
    assert "allages.csv" in (out / "data" / "SHA256SUMS").read_text(encoding="utf-8")
    assert "https://farmaka.info/allages/</loc>" in (out / "sitemap.xml").read_text(encoding="utf-8")


def test_leaflet_flags_and_quick_links():
    from sitegen.leaflets import flags, quick_links

    def sec(num, *texts, heads=()):
        blocks = [{"type": "subheading", "text": h} for h in heads] + [
            {"type": "paragraph", "text": t} for t in texts
        ]
        return {"number": num, "title": "", "blocks": blocks}

    d = {
        "pil": [
            {
                "sections": [
                    sec(
                        "2",
                        heads=(
                            "Προειδοποιήσεις και προφυλάξεις",
                            "Κύηση και θηλασμός",
                            "Οδήγηση και χειρισμός μηχανημάτων",
                        ),
                    ),
                    sec("3", "x"),
                    sec("4", "x"),
                    sec("5", "Φυλάσσετε σε ψυγείο (2°C – 8°C). Μην καταψύχετε."),
                ]
            }
        ],
        "smpc": [
            {
                "preamble": [
                    {"type": "paragraph", "text": "Το φάρμακο αυτό τελεί υπό συμπληρωματική παρακολούθηση."}
                ],
                "sections": [sec("4.1", "x"), sec("6.4", "Φυλάσσετε σε ψυγείο (2°C έως 8°C).")],
            }
        ],
    }
    assert flags(d) == ["Ψυγείο 2–8 °C", "▼ Συμπληρωματική παρακολούθηση"]
    links = dict(quick_links(d, "pil"))
    assert links["Κύηση / θηλασμός"] == "d0-s2-h1" and links["Οδήγηση"] == "d0-s2-h2"
    assert links["Φύλαξη"] == "d0-s5" and "Παιδιά" not in links
    # reconstituted solution kept cold is not a storage condition of the product
    d["pil"][0]["sections"][3] = sec(
        "5", "Μετά την ανασύσταση, φυλάσσετε στο ψυγείο στους 2°C έως 8°C για 24 ώρες."
    )
    d["smpc"][0]["sections"][1] = sec("6.4", "Δεν απαιτούνται ιδιαίτερες συνθήκες φύλαξης.")
    assert "Ψυγείο 2–8 °C" not in flags(d)


def test_equal_strengths_group_together():
    from sitegen.drug import strength

    # same strength written four ways (moh price list, alprazolam)
    keys = {
        strength(n)[0]
        for n in (
            "ALPRAZOLAM/GRINDEKS TABLET 0.5MG/TAB BT X 30 TABS",
            "XANAX TABLET 0,5MG/TAB BT x 30",
            "ALPRAZOLAM/AUROBINDO TABLET 0,50MG/TAB BTx30(BLISTER)",
            "SATURNIL TABLET 0.50MG/TAB BTX30",
        )
    }
    assert keys == {"0.5MG"}
    assert strength("X TABLET 1.0MG/TAB BTx30")[0] == strength("Y TABLET 1MG/TAB BTx30")[0] == "1MG"
    assert strength("XANAX TABLET 0,5MG/TAB BT x 30")[1] == "0,5 mg"
    # '10.000 IU' may be ten thousand: never folded into '10'
    assert strength("DEFILL SOFT.CAPS 10.000 IU/CAP BT X 4")[0] == "10.000IU"


def test_package_page_holds_the_leaflet():
    import re

    from sitegen import pages
    from test_leaflet_glance import ABRAXANE  # same folder (pytest puts it on the path)

    p = _pkg("100000001", "ABRAXANE", 240.42, rp=240.42)
    p["product_information"] = [
        {"product_number": "EMEA/H/C/000778", "smpc_pil_el_url": "https://e.org/a.pdf"}
    ]
    other = _pkg("100000002", "PLAIN", 10.0)
    other["product_information"] = []
    ctx = _ctx([p, other])
    ctx.meta["sources"] = [
        {"published": "2020-06-26", "moh_article": 7309, "url": "https://www.moh.gov.gr/b"}
    ]
    ctx.leaflets = {
        "EMEA/H/C/000778": {
            "slug": "abraxane",
            "name": "Abraxane",
            "pil": True,
            "smpc": True,
            "flags": [],
            "quick": [],
            "texts": {
                "pil": [ABRAXANE],
                "smpc": True,
                "source": {"url": "https://e.org/a.pdf", "last_modified": "Thu, 08 Oct 2026 09:40:54 GMT"},
            },
            "smpc_quick": [("Ενδείξεις", "d0-s4.1")],
        }
    }
    html = pages.package(ctx, p)
    # answers first in the main column, before the price; the whole leaflet below the columns
    assert html.index('class="card glance"') < html.index("Λιανική τιμή") < html.index('id="fyllo"')
    assert "Αυτούσια από το φύλλο οδηγιών · EMA · 08/10/2026" in html
    assert 'href="#fyllo">Φύλλο οδηγιών χρήσης ↓' in html  # the side card points down, not away
    assert 'href="/fylla/abraxane/pxp/"' in html
    ids = set(re.findall(r'id="([^"]+)"', html))
    assert set(re.findall(r'href="#(fo-[^"]+)"', html)) <= ids
    # no leaflet: the page is as before
    plain = pages.package(ctx, other)
    assert "glance" not in plain and 'id="fyllo"' not in plain
