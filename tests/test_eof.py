"""EOF product search crawler (ingest/eof.py), offline.

Fixtures in samples/eof/ are answers of services.eof.gr of 14/11/2026, trimmed (scripts,
tooltips, images and long option lists removed; tests check they still parse like the
originals did):
  home.html                the search form of home.xhtml (ViewState);
  search_0000902.xml       AJAX answer of a search by code 0000902, one row;
  search_name_antivom.xml  AJAX answer of a search by name ANTIVOM, five rows;
  search_none.xml          AJAX answer of a search by an unknown code, no rows;
  view_0000902.html        the product page of 0000902 (packages, SmPC and PIL links);
  view_no_documents_1898705.html    a product page without any document file;
  view_word_documents_1899601.html  a product page whose SmPC and PIL are Word files.
No document is stored: the fake site answers with a few bytes starting with %PDF (or
the Word signature).
"""

import csv
import json
import os
import random
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import requests

from ingest import eof
from ingest.eof import (
    BASE,
    HOME,
    LINK_FIELDS,
    USER_AGENT,
    Blocked,
    BudgetExhausted,
    Client,
    EofFormatError,
    Lock,
    OutsideWindow,
    Policy,
    Session,
    Target,
    ViewExpired,
    crawl,
    ean13_ok,
    in_window,
    links,
    parse_product,
    parse_search,
    plan,
    read_manifest,
    search_fields,
    search_request,
    write_links,
)

SAMPLES = Path(__file__).resolve().parent.parent / "samples" / "eof"
ATH = ZoneInfo("Europe/Athens")
VIEW_ID = "9a858a3bab6c83ff86fc7255e074e62d3d6d3893ae264b237e01d07007855142"
SPC_URL = BASE + "download?id=6f66fbd0916273bc25cb89bdedcd7568&type=1cf1ae247be5a5fbcf4b8ae0e5e87430"
PIL_URL = BASE + "download?id=6f66fbd0916273bc25cb89bdedcd7568&type=24659f655d08ed31db9a5f592f4f43f4"


def sample(name: str) -> str:
    return (SAMPLES / name).read_text(encoding="utf-8")


# --- fakes -------------------------------------------------------------------------


class FakeClock:
    """Monotonic and wall clock in one; sleep() advances it."""

    def __init__(self, t: float = 1_000_000.0, start: datetime | None = None):
        self.t0 = self.t = t
        self.start = start or datetime(2026, 11, 15, 3, 0, tzinfo=ATH)
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.t

    def sleep(self, d: float) -> None:
        self.sleeps.append(d)
        self.t += d

    def now(self) -> datetime:
        return self.start + timedelta(seconds=self.t - self.t0)


def make_policy(fc: FakeClock, **kw) -> Policy:
    return Policy(clock=fc.clock, wall=fc.clock, sleep=fc.sleep, now=fc.now, rng=random.Random(7), **kw)


def resp(status=200, body=b"", ctype="text/html;charset=UTF-8", url=HOME, headers=None) -> requests.Response:
    r = requests.Response()
    r.status_code = status
    r._content = body
    r.url = url
    r.headers.update({"Content-Type": ctype} | (headers or {}))
    return r


class FakeSite:
    """Answers like services.eof.gr from the fixtures; records every request."""

    def __init__(self, fc: FakeClock, products: dict[str, str] | None = None):
        self.fc = fc
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[float, str, str]] = []
        self.override: dict[str, requests.Response] = {}
        # code -> view id; product pages are the 0000902 page with the code replaced
        self.products = products if products is not None else {"0000902": VIEW_ID}

    def request(self, method, url, timeout=None, allow_redirects=True, data=None, headers=None, **kw):
        self.calls.append((self.fc.t, method, url))
        if url in self.override:
            return self.override[url]
        if url == HOME and method == "GET":
            return resp(body=sample("home.html").encode(), url=url)
        if url == HOME and method == "POST":
            assert headers["Faces-Request"] == "partial/ajax"
            code = data["frmSearch:txtDrugid"]
            if code not in self.products:
                return resp(body=sample("search_none.xml").encode(), ctype="text/xml;charset=UTF-8")
            xml = sample("search_0000902.xml").replace("0000902", code).replace(VIEW_ID, self.products[code])
            return resp(body=xml.encode(), ctype="text/xml;charset=UTF-8")
        if url.startswith(BASE + "view.xhtml?id="):
            code = next(c for c, v in self.products.items() if url.endswith(v))
            return resp(body=sample("view_0000902.html").replace("0000902", code).encode(), url=url)
        if url.startswith(BASE + "download?"):
            kind = "SPC" if url == SPC_URL else "PL"
            return resp(
                body=b"%PDF-1.4 fake " + url.encode(),
                ctype="application/octet-stream",
                url=url,
                headers={"Content-Disposition": f'attachment; filename="{kind}_0000902_1.pdf"'},
            )
        return resp(404, b"not found", url=url)

    def urls(self) -> list[str]:
        return [u for _, _, u in self.calls]


# --- pacing, window, budget ----------------------------------------------------------


def test_requests_are_at_least_10s_apart_with_jitter():
    fc = FakeClock()
    site = FakeSite(fc)
    client = Client(make_policy(fc), session=site)
    for url in (HOME, BASE + "view.xhtml?id=" + VIEW_ID, SPC_URL, PIL_URL, HOME, SPC_URL):
        client.request("GET", url)
    times = [t for t, _, _ in site.calls]
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert len(times) == 6
    assert all(10.0 <= g <= 15.0 for g in gaps), gaps
    assert len({round(g, 3) for g in gaps}) > 1  # jitter, not a fixed beat


def test_pacing_continues_across_runs_from_the_request_log(tmp_path):
    fc = FakeClock()
    log = tmp_path / "requests.jsonl"
    log.write_text(json.dumps({"t": fc.t - 3.0, "url": HOME}) + "\n", encoding="utf-8")
    site = FakeSite(fc)
    client = Client(make_policy(fc), log=log, session=site)
    client.request("GET", HOME)
    assert site.calls[0][0] - (fc.t0 - 3.0) >= 10.0
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and json.loads(lines[-1])["status"] == 200


def test_each_redirect_hop_is_a_paced_request():
    fc = FakeClock()
    site = FakeSite(fc)
    site.override[BASE] = resp(302, b"", url=BASE, headers={"Location": "home.xhtml"})
    client = Client(make_policy(fc), session=site)
    r = client.request("GET", BASE)
    assert r.status_code == 200 and site.urls() == [BASE, HOME]
    assert site.calls[1][0] - site.calls[0][0] >= 10.0


@pytest.mark.parametrize(
    "hour,minute,ok",
    [(0, 0, True), (3, 30, True), (6, 59, True), (7, 0, False), (14, 12, False), (23, 59, False)],
)
def test_window_is_midnight_to_seven_athens(hour, minute, ok):
    assert in_window(datetime(2026, 11, 14, hour, minute, tzinfo=ATH)) is ok


def test_window_uses_athens_time_whatever_the_zone_of_the_clock():
    utc = ZoneInfo("UTC")
    assert in_window(datetime(2026, 11, 14, 23, 30, tzinfo=utc))  # 01:30 in Athens
    assert not in_window(datetime(2026, 11, 14, 5, 30, tzinfo=utc))  # 07:30 in Athens


def test_outside_the_window_nothing_is_sent():
    fc = FakeClock(start=datetime(2026, 11, 14, 14, 0, tzinfo=ATH))
    site = FakeSite(fc)
    client = Client(make_policy(fc), session=site)
    with pytest.raises(OutsideWindow):
        client.request("GET", HOME)
    assert site.calls == []


def test_window_closing_while_waiting_stops_before_sending():
    fc = FakeClock(start=datetime(2026, 11, 15, 6, 59, 55, tzinfo=ATH))
    site = FakeSite(fc)
    client = Client(make_policy(fc), session=site)
    client.request("GET", HOME)  # 06:59:55
    with pytest.raises(OutsideWindow):
        client.request("GET", HOME)  # would be 07:00:05 or later
    assert len(site.calls) == 1


def test_testing_budget_allows_exactly_n_requests_outside_the_window():
    fc = FakeClock(start=datetime(2026, 11, 14, 14, 0, tzinfo=ATH))
    site = FakeSite(fc)
    client = Client(make_policy(fc, testing_budget=2), session=site)
    client.request("GET", HOME)
    client.request("GET", HOME)
    with pytest.raises(BudgetExhausted):
        client.request("GET", HOME)
    assert len(site.calls) == 2


def test_user_agent_is_honest():
    fc = FakeClock()
    site = FakeSite(fc)
    Client(make_policy(fc), session=site)
    assert site.headers["User-Agent"] == USER_AGENT
    assert "farmaka-info" in USER_AGENT and "github.com/ppapadeas/farmaka-info" in USER_AGENT
    assert "Mozilla" not in USER_AGENT


# --- stop on blocking ----------------------------------------------------------------


@pytest.mark.parametrize("status", [401, 403, 429, 503])
def test_blocking_status_stops_without_retry(status):
    fc = FakeClock()
    site = FakeSite(fc)
    site.override[HOME] = resp(status, b"no", url=HOME)
    client = Client(make_policy(fc), session=site)
    with pytest.raises(Blocked):
        client.request("GET", HOME)
    assert len(site.calls) == 1


def test_server_error_stops_too():
    fc = FakeClock()
    site = FakeSite(fc)
    site.override[HOME] = resp(500, b"oops", url=HOME)
    with pytest.raises(eof.Stop):
        Client(make_policy(fc), session=site).request("GET", HOME)
    assert len(site.calls) == 1


@pytest.mark.parametrize(
    "body",
    [
        b'<html><div class="g-recaptcha" data-sitekey="x"></div></html>',
        b"<html><title>Request Rejected</title>The requested URL was rejected.</html>",
        b"<html>Too Many Requests</html>",
    ],
)
def test_captcha_or_waf_page_stops(body):
    fc = FakeClock()
    site = FakeSite(fc)
    site.override[HOME] = resp(200, body, url=HOME)
    with pytest.raises(Blocked):
        Client(make_policy(fc), session=site).request("GET", HOME)
    assert len(site.calls) == 1


def test_a_captcha_plugin_on_a_large_eof_gr_page_is_not_a_challenge():
    # www.eof.gr (WordPress) loads the stylesheet of a contact-form math captcha.
    body = (
        b"<html>" + b"x" * 40_000 + b'<link href="/wp-content/plugins/ds-cf7-math-captcha/style.css"></html>'
    )
    eof.check_response(resp(200, body, url="https://www.eof.gr/"))


def test_connection_error_stops_without_retry():
    fc = FakeClock()

    class Dropping(FakeSite):
        def request(self, method, url, **kw):
            self.calls.append((self.fc.t, method, url))
            raise requests.ConnectionError("reset by peer")

    site = Dropping(fc)
    with pytest.raises(Blocked):
        Client(make_policy(fc), session=site).request("GET", HOME)
    assert len(site.calls) == 1


# --- parsing -------------------------------------------------------------------------


def test_search_fields_from_the_home_page():
    f = search_fields(sample("home.html"))
    assert f["javax.faces.ViewState"] == "4701124077595351198:5928676275071614728"
    assert f["frmSearch:txtDrugid"] == "" and f["frmSearch:txtDrname"] == ""
    assert f["frmSearch"] == "frmSearch"
    assert f["frmSearch:j_idt90_input"] == "ANY"  # first option of a select without a selected one
    assert "frmSearch:txtDrroute" not in f  # unchecked checkboxes are not sent
    data = search_request(f, "0000902")
    assert data["frmSearch:txtDrugid"] == "0000902"
    assert data["javax.faces.source"] == "frmSearch:btnSearch"
    assert data["javax.faces.partial.render"] == "frmMain"
    with pytest.raises(ValueError):
        search_request(f, "000090201")  # the search takes the 7-digit product code


def test_parse_search_one_row():
    r = parse_search(sample("search_0000902.xml"))
    assert r.row_count == 1 and r.viewstate == "4701124077595351198:5928676275071614728"
    (row,) = r.rows
    assert (row.code, row.name, row.status, row.procedure) == (
        "0000902",
        "ANTIVOM TABLET 8MG/TAB",
        "Εγκεκριμένο",
        "Εθνική",
    )
    assert row.view_url == BASE + "view.xhtml?id=" + VIEW_ID


def test_parse_search_several_rows_and_none():
    r = parse_search(sample("search_name_antivom.xml"))
    assert [x.code for x in r.rows] == ["0000906", "0000904", "0000902", "0000905", "0000903"]
    assert r.rows[0].licence == "93336/26-07-2019"
    assert all(x.view_url.startswith(BASE + "view.xhtml?id=") for x in r.rows)
    none = parse_search(sample("search_none.xml"))
    assert none.rows == [] and none.row_count == 0


def test_parse_search_rejects_unknown_columns():
    changed = sample("search_0000902.xml").replace("Αρ. άδειας", "Άδεια")
    with pytest.raises(EofFormatError, match="columns"):
        parse_search(changed)


def test_parse_search_view_expired():
    xml = (
        "<?xml version='1.0' encoding='UTF-8'?><partial-response><error>"
        "<error-name>class javax.faces.application.ViewExpiredException</error-name>"
        "<error-message><![CDATA[viewId:/home.xhtml]]></error-message></error></partial-response>"
    )
    with pytest.raises(ViewExpired):
        parse_search(xml)
    with pytest.raises(EofFormatError):
        parse_search("<html>not xml")


def test_parse_product_page():
    p = parse_product(sample("view_0000902.html"), BASE + "view.xhtml?id=" + VIEW_ID)
    assert (p.code, p.name, p.atc) == ("0000902", "ANTIVOM TABLET 8MG/TAB", "N07CA01")
    assert p.mah == "UNI-PHARMA ΚΛΕΩΝ ΤΣΕΤΗΣ ΦΑΡΜΑΚΕΥΤΙΚΑ ΕΡΓΑΣΤΗΡΙΑ ΑΒΕΕ"
    assert p.general["Διαδικασία"] == "Εθνική"
    assert len(p.barcodes) == 6 and all(ean13_ok(b) for b in p.barcodes)
    # our key: the barcode carries the 9-digit EOF code at positions 3-11
    assert p.eof_codes[:3] == ["000090201", "000090202", "000090203"]
    docs = {d.doc_type: d for d in p.documents}
    assert docs["smpc"].url == SPC_URL and docs["smpc"].filename == "SPC_0000902_2.pdf"
    assert docs["smpc"].version == "2"
    assert docs["pil"].url == PIL_URL and docs["pil"].filename == "PL_0000902_1.pdf"
    assert set(docs) == {"smpc", "pil"}  # the assessment report row has no file


def test_parse_product_rejects_another_page():
    with pytest.raises(EofFormatError):
        parse_product(sample("home.html"))


def test_ean13():
    assert ean13_ok("2800000902018")
    assert not ean13_ok("2800000902019")
    assert not ean13_ok("280000090201")


# --- plan ----------------------------------------------------------------------------


def pkg(code: str, name: str = "X TABLET") -> dict[str, str]:
    return {"eof_code": code, "name": name}


def test_plan_most_packages_first_without_ema_products():
    packages = [
        pkg("000090201"),
        pkg("000090203"),
        pkg("000090302"),
        pkg("220880401"),  # EMA
        pkg("220880402"),
        pkg("100000101"),
        pkg("100000102"),
    ]
    targets = plan(packages, {"220880401"})
    assert [(t.code, t.eof_codes) for t in targets] == [
        ("0000902", ("000090201", "000090203")),
        ("1000001", ("100000101", "100000102")),
        ("0000903", ("000090302",)),
    ]
    with pytest.raises(ValueError):
        plan([pkg("90201")], set())


# --- crawl ---------------------------------------------------------------------------


def test_crawl_end_to_end_and_idempotent(tmp_path):
    fc = FakeClock()
    site = FakeSite(fc)
    targets = [
        Target("0000902", ("000090201", "000090203"), "ANTIVOM TABLET 8MG/TAB"),
        Target("9999990", ("999999001",), ""),
    ]
    stats, manifest = crawl(targets, tmp_path, Client(make_policy(fc), session=site))
    assert site.urls() == [HOME, HOME, BASE + "view.xhtml?id=" + VIEW_ID, SPC_URL, PIL_URL, HOME]
    assert (stats.products_done, stats.not_found, stats.documents_fetched) == (1, 1, 2)

    on_disk = read_manifest(tmp_path / "manifest.json")
    assert on_disk["products"] == manifest["products"]
    p = on_disk["products"]["0000902"]
    assert p["state"] == "done" and p["name"] == "ANTIVOM TABLET 8MG/TAB"
    assert p["eof_codes"] == ["000090201", "000090203"] and p["eof_codes_not_on_page"] == []
    assert [d["type"] for d in p["documents"]] == ["smpc", "pil"]
    assert [r["kind"] for r in p["responses"]] == ["search", "view"]
    for r in p["responses"]:  # raw answers cached with their hash
        assert eof._sha256((tmp_path / r["path"]).read_bytes()) == r["sha256"]
    spc = on_disk["documents"][SPC_URL]
    assert spc["path"] == "pdf/SPC_0000902_2.pdf" and spc["version"] == "2"
    assert eof._sha256((tmp_path / spc["path"]).read_bytes()) == spc["sha256"]
    assert spc["retrieved_at"] and spc["served_filename"] == "SPC_0000902_1.pdf"
    assert on_disk["products"]["9999990"]["state"] == "not_found"

    before = (tmp_path / "manifest.json").read_text(encoding="utf-8")
    site2 = FakeSite(fc)
    stats2, _ = crawl(targets, tmp_path, Client(make_policy(fc), session=site2))
    assert site2.calls == []  # nothing is searched or fetched again
    after = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert after["products"] == json.loads(before)["products"]
    assert after["documents"] == json.loads(before)["documents"]
    assert stats2.products_done == 0


def test_interrupted_product_resumes_with_the_documents_only(tmp_path):
    fc = FakeClock()
    targets = [Target("0000902", ("000090201",), "ANTIVOM")]
    site = FakeSite(fc)
    stats, _ = crawl(targets, tmp_path, Client(make_policy(fc, testing_budget=4), session=site))
    assert "budget" in stats.stopped and site.urls()[-1] == SPC_URL
    m = read_manifest(tmp_path / "manifest.json")
    assert m["products"]["0000902"]["state"] == "documents_pending" and list(m["documents"]) == [SPC_URL]

    site2 = FakeSite(fc)
    crawl(targets, tmp_path, Client(make_policy(fc), session=site2))
    assert site2.urls() == [PIL_URL]
    assert read_manifest(tmp_path / "manifest.json")["products"]["0000902"]["state"] == "done"


def test_a_document_url_is_never_fetched_twice(tmp_path):
    fc = FakeClock()
    other = "0" * 32 + "1" * 32
    site = FakeSite(fc, {"0000902": VIEW_ID, "0000903": other})  # same download links on both pages
    targets = [Target("0000902", ("000090201",), ""), Target("0000903", ("000090302",), "")]
    stats, m = crawl(targets, tmp_path, Client(make_policy(fc), session=site))
    assert site.urls().count(SPC_URL) == 1 and site.urls().count(PIL_URL) == 1
    assert (stats.documents_fetched, stats.documents_reused) == (2, 2)
    rows = links(m)
    assert {(r["eof_code"], r["doc_type"]) for r in rows} == {
        ("000090201", "smpc"),
        ("000090201", "pil"),
        ("000090302", "smpc"),
        ("000090302", "pil"),
    }


OTHER_ID = "0" * 32 + "2" * 32
WORD_SPC = BASE + "download?id=93ec8452b69ce8cf976e6665d1f06ce2&type=1cf1ae247be5a5fbcf4b8ae0e5e87430"
WORD_PIL = BASE + "download?id=93ec8452b69ce8cf976e6665d1f06ce2&type=24659f655d08ed31db9a5f592f4f43f4"
OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 64


def site_with_page(fc: FakeClock, code: str, fixture: str) -> FakeSite:
    site = FakeSite(fc, {code: OTHER_ID})
    url = BASE + "view.xhtml?id=" + OTHER_ID
    site.override[url] = resp(body=sample(fixture).encode(), url=url)
    return site


def test_product_without_document_files_is_done_with_none(tmp_path):
    fc = FakeClock()
    site = site_with_page(fc, "1898705", "view_no_documents_1898705.html")
    stats, m = crawl([Target("1898705", ("189870501",), "")], tmp_path, Client(make_policy(fc), session=site))
    p = m["products"]["1898705"]
    assert p["state"] == "done" and p["documents"] == [] and p["atc"] == "B05XA03"
    assert stats.documents_fetched == 0 and links(m) == []
    assert site.urls() == [HOME, HOME, BASE + "view.xhtml?id=" + OTHER_ID]


def test_word_documents_are_kept_as_word_files(tmp_path):
    fc = FakeClock()
    site = site_with_page(fc, "1899601", "view_word_documents_1899601.html")
    for url in (WORD_SPC, WORD_PIL):
        site.override[url] = resp(body=OLE + url.encode(), ctype="application/octet-stream", url=url)
    _, m = crawl([Target("1899601", ("189960101",), "")], tmp_path, Client(make_policy(fc), session=site))
    spc = m["documents"][WORD_SPC]
    assert (spc["format"], spc["path"], spc["version"]) == ("doc", "pdf/SPC_1899601_2.doc", "2")
    assert spc["pdf_modified"] == "" and (tmp_path / spc["path"]).read_bytes().startswith(OLE)
    assert {r["local_path"] for r in links(m, "data/eof")} == {
        "data/eof/pdf/SPC_1899601_2.doc",
        "data/eof/pdf/PL_1899601_2.doc",
    }


def test_an_html_page_instead_of_a_document_is_kept_and_stops(tmp_path):
    fc = FakeClock()
    site = FakeSite(fc)
    site.override[SPC_URL] = resp(body=b"<!DOCTYPE html><html>error</html>", ctype="application/octet-stream")
    with pytest.raises(EofFormatError, match="HTML"):
        crawl([Target("0000902", ("000090201",), "")], tmp_path, Client(make_policy(fc), session=site))
    kept = list((tmp_path / "raw").glob("document_0000902_smpc_*.html"))
    assert len(kept) == 1 and SPC_URL not in read_manifest(tmp_path / "manifest.json")["documents"]


def test_an_unknown_format_is_kept_as_bin_and_reported(tmp_path):
    fc = FakeClock()
    site = FakeSite(fc)
    site.override[SPC_URL] = resp(body=b"\x00\x01binary", ctype="application/octet-stream", url=SPC_URL)
    stats, m = crawl([Target("0000902", ("000090201",), "")], tmp_path, Client(make_policy(fc), session=site))
    assert m["documents"][SPC_URL]["format"] == "unknown"
    assert m["documents"][SPC_URL]["path"].endswith(".bin") and stats.unknown_formats == [SPC_URL]


def test_sniff():
    assert eof.sniff(b"%PDF-1.5\n") == "pdf"
    assert eof.sniff(OLE) == "doc"
    assert eof.sniff(b"PK\x03\x04....[Content_Types].xml word/document.xml") == "docx"
    assert eof.sniff(b"{\\rtf1\\ansi") == "rtf"
    assert eof.sniff(b"  <html><body>") == "html"
    assert eof.sniff(b"\x00\x01") == "unknown"


def test_blocked_mid_crawl_stops_everything(tmp_path):
    fc = FakeClock()
    site = FakeSite(fc)
    site.override[BASE + "view.xhtml?id=" + VIEW_ID] = resp(429, b"slow down", url=BASE + "view.xhtml")
    targets = [Target("0000902", ("000090201",), ""), Target("0000903", ("000090302",), "")]
    with pytest.raises(Blocked):
        crawl(targets, tmp_path, Client(make_policy(fc), session=site))
    assert site.urls() == [HOME, HOME, BASE + "view.xhtml?id=" + VIEW_ID]  # 0000903 never searched
    assert read_manifest(tmp_path / "manifest.json")["products"] == {}


def test_expired_view_gets_one_new_session(tmp_path):
    fc = FakeClock()
    expired = (
        b"<?xml version='1.0' encoding='UTF-8'?><partial-response><error><error-name>"
        b"javax.faces.application.ViewExpiredException</error-name></error></partial-response>"
    )

    class Expiring(FakeSite):
        def request(self, method, url, **kw):
            if method == "POST" and sum(1 for _, m, _ in self.calls if m == "POST") == 0:
                self.calls.append((self.fc.t, method, url))
                return resp(body=expired, ctype="text/xml;charset=UTF-8")
            return super().request(method, url, **kw)

    site = Expiring(fc)
    result, _ = Session(Client(make_policy(fc), session=site)).search("0000902")
    assert [r.code for r in result.rows] == ["0000902"]
    assert [m for _, m, _ in site.calls] == ["GET", "POST", "GET", "POST"]


# --- links ---------------------------------------------------------------------------


def test_links_csv(tmp_path):
    fc = FakeClock()
    _, m = crawl(
        [Target("0000902", ("000090201", "000090203"), "")],
        tmp_path,
        Client(make_policy(fc), session=FakeSite(fc)),
    )
    out = tmp_path / "eof_docs.csv"
    assert write_links(links(m, "data/eof"), out) == 4
    with out.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert tuple(rows[0]) == LINK_FIELDS
    assert rows[0] == {
        "eof_code": "000090201",
        "doc_type": "pil",
        "url": PIL_URL,
        "date": "",  # fake PDF: no metadata date
        "local_path": "data/eof/pdf/PL_0000902_1.pdf",
    }


def test_pdf_date_from_metadata():
    assert eof._pdf_date("D:20260602075832+03'00'") == "2026-06-02"
    assert eof._pdf_date(b"D:20211231095604+02'00'") == "2021-12-31"
    assert eof._pdf_date(None) == ""


# --- command line --------------------------------------------------------------------


def write_inputs(tmp_path: Path) -> tuple[Path, Path]:
    packages = tmp_path / "packages.csv"
    packages.write_text("eof_code,name\n000090201,ANTIVOM TABLET 8MG/TAB BTx50\n", encoding="utf-8")
    ema = tmp_path / "ema_links.csv"
    ema.write_text("eof_code,ema_name\n", encoding="utf-8")
    return packages, ema


def run_main(tmp_path: Path, *extra: str) -> int:
    packages, ema = write_inputs(tmp_path)
    args = ["crawl", "--packages", str(packages), "--ema-links", str(ema), "--out", str(tmp_path / "eof")]
    return eof.main(args + list(extra))


def test_main_refuses_outside_the_window(tmp_path, monkeypatch):
    monkeypatch.setattr(eof, "athens_now", lambda: datetime(2026, 11, 14, 14, 0, tzinfo=ATH))
    monkeypatch.setattr(eof, "new_session", lambda: pytest.fail("no request may be sent"))
    assert run_main(tmp_path) == 2
    assert not (tmp_path / "eof" / "requests.jsonl").exists()


def test_main_refuses_with_a_stop_file(tmp_path, monkeypatch):
    monkeypatch.setattr(eof, "athens_now", lambda: datetime(2026, 11, 15, 3, 0, tzinfo=ATH))
    monkeypatch.setattr(eof, "new_session", lambda: pytest.fail("no request may be sent"))
    (tmp_path / "eof").mkdir()
    (tmp_path / "eof" / "STOP").write_text("EOF asked us to stop", encoding="utf-8")
    assert run_main(tmp_path, "--now-for-testing", "3") == 2


def test_main_exits_non_zero_when_blocked(tmp_path, monkeypatch):
    fc = FakeClock()
    site = FakeSite(fc)
    site.override[HOME] = resp(403, b"Forbidden", url=HOME)
    monkeypatch.setattr(eof, "new_session", lambda: site)
    assert run_main(tmp_path, "--now-for-testing", "3") == 3
    assert len(site.calls) == 1
    log = (tmp_path / "eof" / "requests.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(log[0])["status"] == 403
    assert not (tmp_path / "eof" / ".lock").exists()


def test_lock_allows_one_run_and_takes_over_a_stale_one(tmp_path):
    path = tmp_path / ".lock"
    with Lock(path):
        with pytest.raises(RuntimeError):
            Lock(path).__enter__()
    path.write_text("123", encoding="utf-8")
    old = path.stat().st_mtime - eof.LOCK_STALE_SECONDS - 5
    os.utime(path, (old, old))
    with Lock(path):
        pass
    assert not path.exists()
