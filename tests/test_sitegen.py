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
    assert 'class="spark"' in html
    assert "<script>" not in html  # CSP: no inline scripts


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
