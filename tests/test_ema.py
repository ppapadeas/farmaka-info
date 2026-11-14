"""EMA product-information links (ingest/ema.py), offline.

Fixtures in samples/ema/ were cut from the EMA files of 14/11/2026:
  medicines_sample.xlsx       14 rows of the medicines table (layout and header row as
                              published; the long "Therapeutic indication" text emptied);
  epar_documents_sample.json  14 documents of the EPAR documents file, translations
                              trimmed to "de" and "el".
Package rows below are copied from data/current/packages.csv (columns we read).
"""

import csv
import json
from pathlib import Path

import openpyxl
import pytest
import requests

from ingest import ema
from ingest.ema import (
    LINK_FIELDS,
    Client,
    EmaFormatError,
    EmaProduct,
    attach_pi,
    fetch,
    match_packages,
    name_key,
    read_medicines,
    read_pi_urls,
    spread,
    substance_words,
    verify,
    write_links,
)

SAMPLES = Path(__file__).resolve().parent.parent / "samples" / "ema"
MEDICINES = SAMPLES / "medicines_sample.xlsx"
DOCUMENTS = SAMPLES / "epar_documents_sample.json"
PI = "https://www.ema.europa.eu/el/documents/product-information/"


def pkg(eof_code: str, name: str, substances: str, mah: str = "") -> dict[str, str]:
    return {"eof_code": eof_code, "name": name, "substances": substances, "mah": mah}


XYDALBA = pkg("312080101", "XYDALBA PD.C.SO.IN 500MG/VIAL BTx1 VIAL", "DALBAVANCIN")
HUMIRA = pkg("256960401", "HUMIRA INJ.SOL 40MG/0,4 ML PF.SYR BTx1 PF.SYR. + 1 επίθεμα αλκοόλης", "ADALIMUMAB")
SKYRIZI = pkg("321340201", "SKYRIZI INJ.SOL 150MG/ML BT X 1 PF.PEN", "RISANKIZUMAB")
ABILIFY = pkg("265700202", "ABILIFY TABLET 10MG/TAB BTx28 (σε BLIST ALU/ALU διάτρητα)", "ARIPIPRAZOLE")
ABILIFY_MAINTENA = pkg("307100201", "ABILIFY MAINTENA PD.S.IN.PR 400mg/VIAL 1VIAL+1VIAL SOLV", "ARIPIPRAZOLE")
INCRUSE = pkg(
    "307820102",
    "INCRUSE INH.PD.DOS 55mcg/DOSE-UMECLIDINIUM BTx1 συσκευή εισπνοών (Ellipta) x 30 δόσεις",
    "UMECLIDINIUM BROMIDE",
)
CIMZIA = pkg("291450101", "CIMZIA INJ.SOL 200MG/ML BTx2 PF.SYRx1ML", "CERTOLIZUMAB PEGOL")
LENALIDOMIDE_KRKA = pkg("328370102", "LENALIDOMIDE KRKA CAPS 5MG/CAP 21 Χ 1 ΚΑΨΑΚΙΟ", "LENALIDOMIDE")
BUSCOPAN_PLUS = pkg(
    "210450103", "BUSCOPAN PLUS F.C.TAB (10+500)MG/TAB BT x 40 (BLIST 4x10)", "HYOSCINE|PARACETAMOL"
)


@pytest.fixture(scope="module")
def products() -> list[EmaProduct]:
    _, prods = read_medicines(MEDICINES)
    _, by_number, by_name = read_pi_urls(DOCUMENTS)
    return attach_pi(prods, by_number, by_name)


def by_name(products: list[EmaProduct]) -> dict[str, EmaProduct]:
    return {p.current_name: p for p in products}


# --- EMA files ---------------------------------------------------------------------


def test_read_medicines_keeps_authorised_human_only():
    generated, prods = read_medicines(MEDICINES)
    assert generated == "14/11/2026 - 06:00"
    names = sorted(p.current_name for p in prods)
    # dropped: a veterinary medicine, "Lenalidomide Krka d.d." (withdrawn), Cimzia 000740 (refused)
    assert names == [
        "Abilify",
        "Abilify Maintena",
        "Briviact",
        "Byannli",
        "Cimzia",
        "Humira",
        "Incruse Ellipta",
        "Lenalidomide Krka",
        "Lenalidomide Krka d.d. Novo mesto",
        "Skyrizi",
        "Xydalba",
    ]
    x = by_name(prods)["Xydalba"]
    assert (x.product_number, x.inn, x.active_substance) == (
        "EMEA/H/C/002840",
        "dalbavancin",
        "dalbavancin hydrochloride",
    )
    assert x.epar_url == "https://www.ema.europa.eu/en/medicines/human/EPAR/xydalba"
    assert by_name(prods)["Cimzia"].product_number == "EMEA/H/C/001037"


def test_name_notes_and_previous_names(products):
    b = by_name(products)
    assert b["Byannli"].name == "Byannli (previously Paliperidone Janssen-Cilag International)"
    assert b["Byannli"].previous_names == ["Paliperidone Janssen-Cilag International"]
    assert b["Briviact"].name == "Briviact (in Italy: Nubriveo)"
    assert b["Briviact"].previous_names == []
    # a parenthesis that is part of the name stays
    p = EmaProduct("Clopidogrel Teva (hydrogen sulphate)", "EMEA/H/C/001053", "", "", "", "")
    assert p.current_name == "Clopidogrel Teva (hydrogen sulphate)"


def test_missing_column_fails_loudly(tmp_path):
    wb = openpyxl.load_workbook(MEDICINES)
    ws = wb.active
    for cell in ws[9]:
        if cell.value == "Medicine URL":
            cell.value = "Medicine link"
    bad = tmp_path / "bad.xlsx"
    wb.save(bad)
    with pytest.raises(EmaFormatError, match="Medicine URL"):
        read_medicines(bad)


def test_greek_pi_url_comes_from_documents_file(products):
    b = by_name(products)
    assert b["Xydalba"].pi_el_url == PI + "xydalba-epar-product-information_el.pdf"
    assert b["Humira"].pi_el_url == PI + "humira-epar-product-information_el.pdf"
    assert b["Skyrizi"].pi_el_url == PI + "skyrizi-epar-product-information_el.pdf"
    # the EPAR page slug would give a wrong file name (both URLs below were checked: 404)
    assert b["Byannli"].slug == "byannl"
    assert b["Byannli"].pi_el_url == PI + "byannli-epar-product-information_el.pdf"
    assert b["Briviact"].slug == "briviact-italy-nubriveo"
    assert b["Briviact"].pi_el_url == PI + "briviact-epar-product-information_el.pdf"
    # the documents file files Cimzia under an old product number: found by name
    assert b["Cimzia"].pi_el_url == PI + "cimzia-epar-product-information_el.pdf"
    # "Product information URLs for member states" (xls, also type product-information) is skipped
    assert b["Abilify"].pi_el_url == PI + "abilify-epar-product-information_el.pdf"


def test_no_greek_pi_leaves_url_empty(products):
    p = by_name(products)["Xydalba"]
    assert attach_pi([p], {}, {})[0].pi_el_url == ""


# --- text normalisation ------------------------------------------------------------


def test_name_key_ignores_case_accents_symbols_and_punctuation():
    assert name_key("Abilify Maintena®") == name_key("ABILIFY-MAINTENA") == name_key("abilify maintena")
    assert name_key("GONAL-f") == name_key("GONAL F") == "GONALF"
    assert name_key("Xydalba™") == "XYDALBA"
    assert name_key("Lenalidomide Krka d.d. Novo mesto") == "LENALIDOMIDEKRKADDNOVOMESTO"
    # Greek capitals that look like Latin ones ("ΗUMIRA" starts with a Greek Eta)
    assert name_key("ΗUMIRA") == "HUMIRA"
    # other Greek letters stay: a Greek name does not shrink to a Latin fragment
    assert name_key("ΔΕΠΟΝ") == "ΔEΠON"


def test_substance_words_keep_the_main_word():
    assert substance_words("dalbavancin hydrochloride") == {"DALBAVANCIN"}
    assert substance_words("lenalidomide hydrochloride monohydrate") == {"LENALIDOMIDE"}
    assert substance_words("certolizumab pegol") == {"CERTOLIZUMAB"}
    assert substance_words("HYOSCINE PARACETAMOL") == {"HYOSCINE", "PARACETAMOL"}


# --- matching ----------------------------------------------------------------------


def test_true_matches(products):
    m = match_packages([XYDALBA, HUMIRA, SKYRIZI, CIMZIA], products)
    got = {k.eof_code: (k.product.current_name, k.matched_by) for k in m.links}
    assert got == {
        "312080101": ("Xydalba", "name+substance"),
        "256960401": ("Humira", "name+substance"),
        "321340201": ("Skyrizi", "name+substance"),
        "291450101": ("Cimzia", "name+substance"),
    }
    assert not m.ambiguous and not m.name_only


def test_name_alone_is_not_enough(products):
    wrong = pkg("999999901", "XYDALBA PD.C.SO.IN 500MG/VIAL BTx1 VIAL", "VANCOMYCIN HYDROCHLORIDE")
    salt_only = pkg("999999902", "XYDALBA PD.C.SO.IN 500MG/VIAL BTx1 VIAL", "VANCOMYCIN|HYDROCHLORIDE")
    m = match_packages([wrong, salt_only], products)
    assert m.links == []
    assert [r["eof_code"] for r in m.name_only] == ["999999901", "999999902"]
    assert m.name_only[0]["ema"] == "EMEA/H/C/002840 Xydalba"


def test_brand_names_several_products_gives_no_link(products):
    # "Lenalidomide Krka" is one product's name and another one's previous name
    m = match_packages([LENALIDOMIDE_KRKA], products)
    assert m.links == []
    assert m.ambiguous == [
        {
            "eof_code": "328370102",
            "name": LENALIDOMIDE_KRKA["name"],
            "brand": "LENALIDOMIDE KRKA",
            "candidates": [
                "EMEA/H/C/005348 Lenalidomide Krka",
                "EMEA/H/C/005734 Lenalidomide Krka d.d. Novo mesto",
            ],
        }
    ]


def test_registered_sign_and_hyphen(products):
    styled = pkg("307100201", "ABILIFY-MAINTENA® PD.S.IN.PR 400mg/VIAL 1VIAL+1VIAL SOLV", "ARIPIPRAZOLE")
    m = match_packages([styled, ABILIFY_MAINTENA, ABILIFY], products)
    assert [(k.eof_code, k.product.current_name) for k in m.links] == [
        ("307100201", "Abilify Maintena"),
        ("307100201", "Abilify Maintena"),
        ("265700202", "Abilify"),
    ]


def test_previous_name(products):
    m = match_packages([INCRUSE], products)
    assert [(k.product.current_name, k.matched_by) for k in m.links] == [
        ("Incruse Ellipta", "previous_name+substance")
    ]


def test_national_product_is_not_linked(products):
    m = match_packages([BUSCOPAN_PLUS], products)
    assert (m.links, m.ambiguous, m.name_only) == ([], [], [])


def test_write_links(tmp_path, products):
    out = tmp_path / "ema_links.csv"
    write_links(match_packages([XYDALBA, HUMIRA], products).links, out)
    with out.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    assert tuple(rows[0]) == LINK_FIELDS
    assert rows[1:] == [
        [
            "256960401",
            "Humira",
            "EMEA/H/C/000481",
            "adalimumab",
            PI + "humira-epar-product-information_el.pdf",
            "https://www.ema.europa.eu/en/medicines/human/EPAR/humira",
            "name+substance",
        ],
        [
            "312080101",
            "Xydalba",
            "EMEA/H/C/002840",
            "dalbavancin",
            PI + "xydalba-epar-product-information_el.pdf",
            "https://www.ema.europa.eu/en/medicines/human/EPAR/xydalba",
            "name+substance",
        ],
    ]


def test_main_offline(tmp_path, capsys):
    packages = tmp_path / "packages.csv"
    rows = [XYDALBA, HUMIRA, SKYRIZI, ABILIFY, INCRUSE, LENALIDOMIDE_KRKA, BUSCOPAN_PLUS]
    with packages.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(XYDALBA))
        w.writeheader()
        w.writerows(rows)
    out = tmp_path / "ema_links.csv"
    argv = ["--packages", str(packages), "--out", str(out), "--medicines", str(MEDICINES)]
    assert ema.main([*argv, "--documents", str(DOCUMENTS), "--cache", str(tmp_path / "cache")]) == 0
    report = json.loads((tmp_path / "ema_links_report.json").read_text(encoding="utf-8"))
    assert report["ema_authorised_products"] == 11
    assert (report["packages_linked"], report["products_linked"]) == (5, 5)
    assert len(report["ambiguous"]) == 1 and report["name_only_rejected"] == []
    assert report["sources"][0]["generated"] == "14/11/2026 - 06:00"
    assert not (tmp_path / "cache").exists()  # local files given: nothing downloaded
    assert "packages linked: 5" in capsys.readouterr().out


# --- network code, with a fake session -----------------------------------------------


def response(status: int, ctype: str = "", body: bytes = b"", **headers: str) -> requests.Response:
    r = requests.Response()
    r.status_code = status
    r.headers.update({"Content-Type": ctype, **headers} if ctype else headers)
    r._content = body
    r._content_consumed = True
    return r


class FakeSession:
    def __init__(self, answers: dict[str, list[requests.Response]]):
        self.answers = answers
        self.calls: list[tuple[str, str]] = []

    def request(self, method: str, url: str, **kw) -> requests.Response:
        self.calls.append((method, url))
        return self.answers[url].pop(0)


def test_spread_is_deterministic():
    urls = [f"u{i:02d}" for i in range(10)] + ["u03"]
    assert spread(urls, 3) == ["u00", "u03", "u06"]
    assert spread(urls, 50) == sorted(set(urls))


def test_verify_reports_failures_and_waits_on_429():
    ok, gone, busy = PI + "a_el.pdf", PI + "b_el.pdf", PI + "c_el.pdf"
    s = FakeSession(
        {
            ok: [response(200, "application/pdf")],
            gone: [response(404, "text/html; charset=utf-8")],
            busy: [response(429, **{"Retry-After": "0"}), response(200, "application/pdf")],
        }
    )
    failures = verify(Client(pause=0, session=s), [ok, gone, busy])
    assert failures == [(gone, "HTTP 404 text/html; charset=utf-8")]
    assert s.calls == [("HEAD", ok), ("HEAD", gone), ("HEAD", busy), ("HEAD", busy)]


def test_fetch_uses_cache_and_keeps_every_new_download(tmp_path):
    url = ema.MEDICINES_XLSX
    bodies = [b"first", b"first", b"second"]
    s = FakeSession({url: [response(200, "application/octet-stream", b) for b in bodies]})
    c = Client(pause=0, session=s)
    first = fetch(c, url, tmp_path)
    assert fetch(c, url, tmp_path) == first  # cached: no request
    assert len(s.calls) == 1
    assert fetch(c, url, tmp_path, refresh=True) == first  # same bytes: earlier file kept
    second = fetch(c, url, tmp_path, refresh=True)
    assert len(s.calls) == 3
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert [e["file"] for e in manifest] == [first.name, second.name]
    assert manifest[0]["sha256"] == "a7937b64b8caa58f03721bb6bacf5c78cb235febe0e70b1b84cd99541461a08e"
    assert first.read_bytes() == b"first" and second.read_bytes() == b"second"
    assert first.name.startswith("medicines-output-medicines-report_en_") and first.suffix == ".xlsx"
    assert sorted(f.name for f in tmp_path.iterdir()) == sorted([first.name, second.name, "manifest.json"])
