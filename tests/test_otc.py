"""Non-prescription medicines (ΜΗ.ΣΥ.ΦΑ.): classification, parsing, merge, crawl.

No network: the two small bulletins in samples/otc/ are official files as
downloaded; everything else is written with openpyxl in tmp_path."""

import csv
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import openpyxl
import pytest

from ingest import fetch, moh, otc
from ingest.moh import Article, Attachment, classify, classify_otc, read_manifest
from ingest.normalize import UnknownHeader, _key

SAMPLES = Path(__file__).resolve().parent.parent / "samples" / "otc"
Q1_2026 = SAMPLES / "moh_14636_nea_mhsyfa_2026-q1.xlsx"  # moh article 14636
Q34_2025 = SAMPLES / "moh_14109_nea_mhsyfa_2025-q3q4.xlsx"  # moh article 14109

HEADER = [
    "Κωδικός",
    "BARCODE",
    "Περιγραφή Προϊόντος",
    "ATC",
    "Δραστική/ες",
    "KAK",
    "ΜΗΣΥΦΑ",
    "Ενδεικτική Λιανική Τιμή",
    "ΦΠΑ",
]


def barcode(code: str) -> str:
    """EAN-13 with the 280 prefix that embeds a 9-digit EOF code."""
    b = "280" + code
    d = [int(x) for x in b]
    return b + str((10 - sum(d[i] * (3 if i % 2 else 1) for i in range(12)) % 10) % 10)


def row(code: str, price, name: str = "", substance: str = "PARACETAMOL") -> list:
    return [code, barcode(code), name or f"PRODUCT {code}", "N02BE01", substance, "MAH", "N", price, 0.06]


def xlsx(path: Path, rows: list[list], header: list = HEADER) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(header)
    for r in rows:
        ws.append(r)
    wb.save(path)
    return path


# --- classification --------------------------------------------------------


@pytest.mark.parametrize(
    "title,kind",
    [
        ("Δελτίο Τιμών Νέων Μη Συνταγογραφούμενων Φαρμάκων (ΜΗ.ΣΥ.ΦΑ.) 1ου Τριμήνου 2026", "new"),
        (
            "«Δελτίο Τιμών Νέων Μη Συνταγογραφούμενων Φαρμάκων (ΜΗ.ΣΥ.ΦΑ.) 2ου – 3ου – 4ου Τριμήνου 2024»",
            "new",
        ),
        ("Δελτίο Τιμών Νέων ΜΗΣΥΦΑ έτους 2019 και Ιανουαρίου - Φεβρουαρίου 2020", "new"),
        (
            "Κατάλογος ενδεικτικών τιμών λιανικής πώλησης νέων μη συνταγογραφούμενων φαρμάκων "
            "(ΜΗ.ΣΥ.ΦΑ.), 4ο Τρίμηνο 2018 - 20_3_2019",
            "new",
        ),
        ("«Αναθεώρηση καταλόγου μη συνταγογραφούμενων φαρμάκων (ΜΗ.ΣΥ.ΦΑ.) για το έτος 2025»", "repricing"),
        ("«Αναθεώρηση Δελτίου Τιμών Μη Συνταγογραφούμενων Φαρμάκων (ΜΗ.ΣΥ.ΦΑ.), έτους 2019»", "repricing"),
        (
            "Πρώτη Αναθεώρηση έτους 2018 καταλόγου ενδεικτικών τιμών λιανικής πώλησης και ανώτατων "
            "νοσοκομειακών τιμών, μη συνταγογραφούμενων φαρμάκων (ΜΗ.ΣΥ.ΦΑ.) - 19_7_2018",
            "repricing",
        ),
        (
            "Κατάλογος ενδεικτικών τιμών λιανικής πώλησης μη συνταγογραφούμενων φαρμάκων (ΜΗ.ΣΥ.ΦΑ.) "
            "2η ΟΡΘΗ ΕΠΑΝΑΛΗΨΗ (14-07-2017)",
            "repricing",
        ),
        (
            "«Επικαιροποίηση Δελτίου Τιμών Μη Συνταγογραφούμενων Φαρμάκων (ΜΗ.ΣΥ.ΦΑ.) λόγω διοικητικών "
            "μεταβολών».",
            "update",
        ),
        (
            "ΟΡΘΗ ΕΠΑΝΑΛΗΨΗ: Επικαιροποίηση Δελτίου Τιμών Μη Συνταγογραφούμενων Φαρμάκων (ΜΗ.ΣΥ.ΦΑ.) "
            "λόγω διοικητικών μεταβολών",
            "update",
        ),
        ("Επικαιροποίηση Δελτίων Τιμών ΜΗΣΥΦΑ λόγω Διοικητικών Μεταβολών 15_2_2019", "update"),
        (
            "Δελτίο Τιμών Νέων Μη Συνταγογραφούμενων Φαρμάκων (ΜΗ.ΣΥ.ΦΑ.) Δ΄ Τριμήνου 2021 και "
            "Επικαιροποίηση Δελτίου Τιμών Μη Συνταγογραφούμενων Φαρμάκων (ΜΗ.ΣΥ.ΦΑ.) λόγω διοικητικών μεταβολών",
            "update",
        ),
        (
            "Ορθή Επανάληψη της Δ3(α) 6320/11-3-2024 Απόφασης με θέμα: «Δελτίο Τιμών Νέων Μη "
            "Συνταγογραφούμενων Φαρμάκων (ΜΗ.ΣΥ.ΦΑ.) 2ου Τριμήνου 2023», ως προς την Ενδεικτική Λιανική "
            "Τιμή του ΜΗ.ΣΥ.ΦΑ. με κωδικό ΕΟΦ 330940101",
            "amendment",
        ),
        ("Τροποποίηση της Δ3(α) 38203/10-9-2025 απόφασης", "amendment"),
        ("Ανακοίνωση για τα ΜΗ.ΣΥ.ΦΑ.", "other"),
    ],
)
def test_classify_otc(title, kind):
    assert classify_otc(title) == kind


def test_price_classification_unchanged():
    # the price section keeps its own rules
    assert classify("Δελτίο αναθεωρημένων τιμών φαρμάκων ανθρώπινης χρήσης, Δεκεμβρίου 2025") == "revision"
    assert (
        classify("Ο.Ε. Επικαιροποίηση Δελτίου Τιμών Φαρμάκων με ενσωμάτωση Διοικητικών Μεταβολών") == "full"
    )


# --- headers and codes -----------------------------------------------------


def test_header_variants_fold():
    m = otc.HEADER_MAP
    assert m[_key("Κωδικός Προιόντος")] == m[_key("ΚΩΔΙΚΟΣ")] == "eof_code"  # 12249 (2023)
    assert m[_key("ΚΩΔΙΚΟΣ ΦΑΡΜΑΚΟΥ")] == m[_key("ΚΩΔΙΚΟΣ ΠΡΟΪΟΝΤΟΣ")] == "eof_code"  # 7572, 5412
    assert m[_key("ΟΝΟΜΑΣΙΑ ΠΡΟΪΟΝΤΟΣ ")] == m[_key("Περιγραφή ΠροΪόντος")] == "name"  # 14109, 8240
    assert m[_key("ATCCODE")] == "atc"  # 11937
    for h in (
        " Ενδεικτική Λιανική Τιμή",  # 13173
        "Ενδεικτική λιανική τιμή",  # 12248
        "Ενδεικτική Λιανική Τιμή (€)",  # 11378
        "Προτεινόμενη ενδεικτική λιανική τιμή (€)",  # 10974
        "ΠΡΟΤΕΙΝΟΜΕΝΗ ΕΝΔΕΙΚΤΙΚΗ ΛΙΑΝΙΚΗ ΤΙΜΗ",  # 7572
        "Προτειν/νη Ενδεικτική Λ Τ",  # 5178, 8060
        "Λιανική Τιμή",  # 14636 (2026)
    ):
        assert m[_key(h)] == "price_retail_indicative", h
    assert m[_key("Δραστική/ές Ουσία/ες")] == m[_key("ΔΡΑΣΤΙΚΗ/ΕΣ ΟΥΣΙΑ/ΕΣ")] == "substance"
    assert m[_key("Δραστική Ουσία")] == m[_key("ΔΡΑΣΤΙΚΗ ΟΥΣΙΑ")] == "substance"  # 13173, 5412
    assert m[_key("ΜΗ.ΣΥ.ΦΑ.")] == m[_key("ΜΗΣΥΦΑ")] == "note_otc"
    assert m[_key("Νέο Προϊόν")] == m[_key("ΝΈΟ ΠΡΟΪΟΝ")] == "note_new"
    assert m[_key("Τιμή Παραγωγού")] == "price_exfactory"
    assert otc.AMBIGUOUS[_key("ΠΡΟΪΟΝ")] == ("name", "note_product_type")
    assert otc.AMBIGUOUS[_key("KAK")] == otc.AMBIGUOUS[_key("ΚΑΚ")] == ("mah", "note_mah_code")


def test_ambiguous_headers_follow_the_other_columns(tmp_path):
    # 13173: "ΠΡΟΪΟΝ" is the name; 5800: the product type, next to "ΠΕΡΙΓΡΑΦΗ ΠΡΟΪΟΝΤΟΣ"
    a = ["Κωδικός", "BARCODE", "ΠΡΟΪΟΝ", "ATC", " Ενδεικτική Λιανική Τιμή", "Δραστική Ουσία", "ΚΑΚ"]
    r = ["328740101", barcode("328740101"), "IBEROGAST N", "A03", 6.52, "X", "BAYER ΕΛΛΑΣ ΑΒΕΕ"]
    (got,) = otc.read_otc(xlsx(tmp_path / "a.xlsx", [r], header=a))
    assert (got.name, got.mah) == ("IBEROGAST N", "BAYER ΕΛΛΑΣ ΑΒΕΕ")
    b = ["ΚΩΔΙΚΟΣ", "BARCODE", "ΠΕΡΙΓΡΑΦΗ ΠΡΟΪΟΝΤΟΣ", "ΠΡΟΪΟΝ", "KAK", "ΕΝΔΕΙΚΤΙΚΗ ΛΙΑΝΙΚΗ ΤΙΜΗ"]
    r = ["230480201", barcode("230480201"), "CORUBIN", "ΦΑΡΜΑΚΕΥΤΙΚΟ ΠΡΟΪΟΝ", "ΒΙΑΝ ΑΕ", 10.44]
    (got,) = otc.read_otc(xlsx(tmp_path / "b.xlsx", [r], header=b))
    assert (got.name, got.mah) == ("CORUBIN", "ΒΙΑΝ ΑΕ")


def test_2017_layout_code_and_pack_title_row(tmp_path):
    # 4821, 4834 (07/2017): product code and pack in two columns, holder code and name;
    # 5412, 5720: a title row above the header
    header = ["BARCODE", "ΚΩΔΙΚΟΣ", "ΣΥΣΚ.", "Ονομασία Μορφή Περιεκτικότητα", "ATC", "ΔΡΑΣΤΙΚΗ(ΕΣ)"]
    header += ["ΜΗΣΥΦΑ", "ΕΝΔΕΙΚΤΙΚΗ ΛΙΑΝΙΚΗ ΤΙΜΗ", "ΚΑΚ", "ΟΝΟΜΑΣΙΑ ΚΑΚ"]
    r = ["2800007902011", "0007902", "01", "BISOLVON TAB 8MG/TAB BTx20", "R05CB02", "BROMHEXINE"]
    r += ["N", 2.56, "0000000003", "BOEHRINGER INGELHEIM ΕΛΛΑΣ ΑΕ"]
    wb = openpyxl.Workbook()
    wb.active.append(["ΠΙΝΑΚΑΣ"])
    wb.active.append(header)
    wb.active.append(r)
    wb.save(tmp_path / "x.xlsx")
    (got,) = otc.read_otc(tmp_path / "x.xlsx")
    assert (got.eof_code, got.mah, got.price_retail_indicative) == (
        "000790201",
        "BOEHRINGER INGELHEIM ΕΛΛΑΣ ΑΕ",
        2.56,
    )
    assert otc.validate({got.eof_code: got}) == []


def test_two_cell_row_above_the_header_raises(tmp_path):
    wb = openpyxl.Workbook()
    wb.active.append(["ΠΙΝΑΚΑΣ", "ΣΗΜΕΙΩΣΗ"])
    wb.active.append(HEADER)
    wb.save(tmp_path / "x.xlsx")
    with pytest.raises(UnknownHeader, match="header row"):
        otc.read_otc(tmp_path / "x.xlsx")


def test_null_atc_and_stray_marks(tmp_path):
    r = row("022830102", 7.16)
    r[0], r[1], r[3] = "΄022830102", "'" + r[1], "(null)"  # 7829; 8240; 6740, 8060, 9189
    fixes: list[dict] = []
    (got,) = otc.read_otc(xlsx(tmp_path / "x.xlsx", [r]), [], fixes)
    assert (got.eof_code, got.barcode, got.atc) == ("022830102", barcode("022830102"), None)
    assert [f["fix"] for f in fixes] == [
        "stray mark before the eof_code removed",
        "stray mark before the barcode removed",
    ]


def test_same_code_twice_in_one_article(tmp_path):
    # 8484: the complete list and the new products, the second without VAT
    a = xlsx(tmp_path / "a.xlsx", [row("196370301", 37.9)])
    b = xlsx(tmp_path / "b.xlsx", [row("196370301", 37.9)[:7] + [37.9]], header=HEADER[:8])
    rows = otc._read_article([a, b], [], [])
    assert rows["196370301"].vat == 0.06
    c = xlsx(tmp_path / "c.xlsx", [row("196370301", 39.0)])
    issues: list[dict] = []
    assert otc._read_article([a, c], issues, []) == {}  # two prices: neither applied
    assert issues[0]["problem"] == "listed twice in one article: 37.9 and 39.0"


def test_unknown_header_raises(tmp_path):
    p = xlsx(tmp_path / "x.xlsx", [row("023280605", 2.25)], header=HEADER[:-1] + ["Παρατηρήσεις"])
    with pytest.raises(UnknownHeader, match="Παρατηρήσεις"):
        otc.read_otc(p)


def test_missing_required_column_raises(tmp_path):
    p = xlsx(tmp_path / "x.xlsx", [r[:7] for r in [row("023280605", 2.25)]], header=HEADER[:7])
    with pytest.raises(UnknownHeader, match="price_retail_indicative"):
        otc.read_otc(p)


def test_eof_code_from_numeric_cell_and_barcode():
    # numeric cell that lost its leading zero (14636, DEPON SYR): the barcode embeds 023280605
    assert otc.eof_code_of(23280605, "2800232806054") == (
        "023280605",
        "eof_code lost its leading zeros; restored from the barcode",
    )
    assert otc.eof_code_of("338280101\n", "2803382801016") == ("338280101", None)  # 14109
    assert otc.eof_code_of(None, "2800232806054")[0] == "023280605"
    # padding needs a barcode that confirms it
    assert otc.eof_code_of(23280605, "2800232806055") == ("23280605", None)
    assert otc.eof_code_of(23280605, "2801232806053")[0] == "23280605"


def test_row_without_valid_code_goes_to_review(tmp_path):
    bad = row("023280605", 2.25)
    bad[0], bad[1] = 23280605, "2800232806055"  # short code, barcode check digit wrong
    p = xlsx(tmp_path / "x.xlsx", [bad, row("189152102", 2.81)])
    issues: list[dict] = []
    rows = otc.read_otc(p, issues)
    assert [r.eof_code for r in rows] == ["189152102"]
    assert issues == [
        {
            "file": "x.xlsx",
            "eof_code": "23280605",
            "name": "PRODUCT 023280605",
            "problem": "eof_code '23280605' is not 9 digits",
        }
    ]
    with pytest.raises(ValueError, match="not 9 digits"):
        otc.read_otc(p)  # without a review queue: loud


def test_validate_barcode_must_embed_code(tmp_path):
    r = row("189152102", 2.81)
    r[1] = barcode("189152103")
    rows = {x.eof_code: x for x in otc.read_otc(xlsx(tmp_path / "x.xlsx", [r]))}
    assert otc.validate(rows) == [f"189152102: barcode {r[1]} does not embed eof_code"]


# --- real bulletins --------------------------------------------------------


def test_new_products_2026_q1():
    fixes: list[dict] = []
    rows = otc.read_otc(Q1_2026, [], fixes)
    by = {r.eof_code: r for r in rows}
    assert sorted(by) == ["023280605", "189152102", "268540102"]
    depon = by["023280605"]
    assert depon.name == "DEPON SYR 120MG/5ML FLx150ML" and depon.barcode == "2800232806054"
    assert (depon.atc, depon.substances, depon.mah) == ("N02BE01", ["PARACETAMOL"], "UPSA SAS, FRANCE")
    assert depon.price_retail_indicative == 2.25
    assert depon.vat is None and depon.price_hospital_max is None  # no such columns in 14636
    assert by["189152102"].name.startswith("PANADOL® MAXIMUM F.C.TAB 1G/TAB")
    assert [f["eof_code"] for f in fixes] == ["023280605"]
    assert otc.validate(by) == []


def test_new_products_2025_q3_q4():
    rows = otc.read_otc(Q34_2025, [], [])
    assert len(rows) == 22
    by = {r.eof_code: r for r in rows}
    # codes as text with stray whitespace and newlines
    assert "338280101" in by and "331660101" in by
    assert by["023280803"].price_retail_indicative == 4.3 and by["023280803"].vat == 0.06
    assert by["300860104"].substances == ["CINNARIZINE", "DIMENHYDRINATE"]
    assert by["338280101"].mah == "AFLOFARM FARMACJA POLSKA SP. Z O.O., POLAND"  # leading space dropped
    assert otc.validate(by) == []


# --- merge -----------------------------------------------------------------


def test_merge_newest_complete_list_plus_new_products(tmp_path):
    old_full = xlsx(tmp_path / "a.xlsx", [row("100000101", 5.0), row("100000201", 6.0)])
    new_before = xlsx(tmp_path / "b.xlsx", [row("100000301", 7.0)])
    full = xlsx(tmp_path / "c.xlsx", [row("100000101", 5.5), row("100000301", 7.0), row("100000401", 3.0)])
    new_after = xlsx(tmp_path / "d.xlsx", [row("100000501", 9.0), row("100000401", 3.2, name="RENAMED")])
    sources = [
        ("2026-03-01", 40, "new", new_after),
        ("2025-01-10", 10, "repricing", old_full),
        ("2025-06-01", 20, "new", new_before),
        ("2025-09-10", 30, "repricing", full),
    ]
    report: dict = {}
    base, rows = otc.merge(sources, [], [], report)
    assert base == ("2025-09-10", 30, "repricing")
    assert sorted(rows) == ["100000101", "100000301", "100000401", "100000501"]
    assert rows["100000101"].price_retail_indicative == 5.5  # repriced
    assert (rows["100000101"].published, rows["100000101"].moh_article) == ("2025-09-10", 30)
    assert (rows["100000501"].published, rows["100000501"].moh_article) == ("2026-03-01", 40)
    assert rows["100000401"].name == "RENAMED" and rows["100000401"].moh_article == 40
    assert [x["key"][1] for x in report["applied"]] == [30, 40]
    assert report["relisted"][0]["eof_code"] == "100000401"
    # the previous complete list had 100000201; the baseline does not: reported, not resolved
    assert report["dropped"] == [{"eof_code": "100000201", "name": "PRODUCT 100000201", "last_listed_in": 10}]
    assert otc.merge(sources, [], [], {})[1] == rows  # deterministic


def test_merge_takes_an_update_list_as_baseline(tmp_path):
    full = xlsx(tmp_path / "a.xlsx", [row("100000101", 5.0)])
    update = xlsx(tmp_path / "b.xlsx", [row("100000101", 5.0, name="NEW MAH NAME")])
    base, rows = otc.merge([("2025-09-10", 30, "repricing", full), ("2025-11-27", 31, "update", update)])
    assert base[1] == 31 and rows["100000101"].name == "NEW MAH NAME"


def test_merge_without_complete_list_stops(tmp_path):
    with pytest.raises(SystemExit):
        otc.merge([("2026-03-01", 40, "new", xlsx(tmp_path / "d.xlsx", [row("100000501", 9.0)]))])


# --- command line ----------------------------------------------------------


def manifest_entry(aid: int, date: str, kind: str, files: list[str]) -> dict:
    url = f"{moh.OTC_BULLETINS}/{aid}-x"
    return {
        "article_id": aid,
        "date": date,
        "title": f"title {aid}",
        "url": url,
        "kind": kind,
        "attachments": [
            {"fdl": f"{aid}{i}", "label": f"{f} Μέγεθος: 1 KB", "url": f"{url}?fdl={aid}{i}"}
            for i, f in enumerate(files)
        ],
        "checked": True,
    }


def test_main_writes_list_and_report(tmp_path, monkeypatch):
    raw, out = tmp_path / "raw", tmp_path / "current"
    raw.mkdir()
    out.mkdir()
    xlsx(raw / "moh_30_300.xlsx", [row("023280605", 2.1, name="DEPON SYR"), row("100000101", 5.0)])
    (raw / "moh_31_310.pdf").write_bytes(b"%PDF")  # complete list published only as pdf
    (raw / "moh_40_400.xlsx").write_bytes(Q1_2026.read_bytes())
    manifest = [
        manifest_entry(40, "03/09/2026", "new", ["Νέα ΜΗΣΥΦΑ.xlsx"]),
        manifest_entry(35, "01/05/2026", "amendment", ["ΟΕ.pdf"]),
        manifest_entry(31, "27/11/2025", "update", ["Πίνακας.pdf"]),
        manifest_entry(30, "10/09/2025", "repricing", ["Αναθεώρηση.xlsx"]),
    ]
    (tmp_path / "manifest_otc.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    with (out / "packages.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["eof_code", "name", "price_retail", "price_moh_article"])
        w.writerow(["100000101", "PRESCRIPTION PACK", "6.5", "14818"])
    argv = [
        "ingest.otc",
        "--manifest",
        str(tmp_path / "manifest_otc.json"),
        "--raw",
        str(raw),
        "--out",
        str(out),
    ]
    monkeypatch.setattr(sys, "argv", argv)
    otc.main()

    with (out / "otc_packages.csv").open(encoding="utf-8", newline="") as fh:
        got = list(csv.DictReader(fh))
    assert list(got[0]) == otc.OTC_FIELDS
    by = {r["eof_code"]: r for r in got}
    assert sorted(by) == ["023280605", "100000101", "189152102", "268540102"]
    assert by["023280605"]["price_retail_indicative"] == "2.25"  # the 2026 bulletin wins
    assert (by["023280605"]["published"], by["023280605"]["moh_article"]) == ("2026-09-03", "40")
    assert by["023280605"]["vat"] == "0.06"  # kept from the baseline: 14636 has no VAT column
    assert by["100000101"]["moh_article"] == "30" and by["100000101"]["price_hospital_max"] == ""
    report = json.loads((out / "otc_report.json").read_text(encoding="utf-8"))
    assert report["baseline"]["moh_article"] == 30
    assert [s["moh_article"] for s in report["sources"]] == [30, 40]
    assert report["counts"]["packages"] == 4 and report["counts"]["from_new_bulletins"] == 3
    assert {p["moh_article"]: p["reason"] for p in report["pending_bulletins"]} == {
        31: "no xlsx (only pdf); not read",
        35: "needs manual review",
    }
    both = report["also_in_price_list"]
    assert both["count"] == 1 and both["packages"][0]["eof_code"] == "100000101"
    assert both["packages"][0]["price_list_price_retail"] == 6.5


# --- crawl -----------------------------------------------------------------


def test_wanted_pdf_only_without_xlsx():
    url = f"{moh.OTC_BULLETINS}/1-x"
    art = Article(1, "01/01/2026", "t", url, "update")
    art.attachments = [
        Attachment("1", "YA.pdf Μέγεθος: 1 MB", url),
        Attachment("2", "Πίνακας.pdf Μέγεθος: 1 MB", url),
    ]
    assert [a.fdl for a in fetch.wanted(art, False, pdf_if_no_xlsx=True)] == ["1", "2"]
    assert fetch.wanted(art, False) == []
    art.attachments.append(Attachment("3", "Πίνακας.xlsx Μέγεθος: 1 MB", url))
    assert [a.fdl for a in fetch.wanted(art, False, pdf_if_no_xlsx=True)] == ["3"]


def test_daily_scope_uses_the_otc_complete_lists():
    def art(aid, date, kind):
        return Article(aid, date, "t", f"u/{aid}", kind)

    arts = [art(4, "01/04/2026", "new"), art(3, "01/03/2026", "update"), art(2, "01/02/2026", "new")]
    assert [x.article_id for x in fetch.daily_scope(arts, [], otc.OTC_BASELINE_KINDS)] == [4, 3]


class FakeOtcSite:
    """Stand-in for the ΜΗ.ΣΥ.ΦΑ. listing: one page, then the same page again."""

    LISTING = [
        (
            14636,
            "03/09/2026",
            "Δελτίο Τιμών Νέων Μη Συνταγογραφούμενων Φαρμάκων (ΜΗ.ΣΥ.ΦΑ.) 1ου Τριμήνου 2026",
        ),
        (
            13833,
            "27/11/2025",
            "ΟΡΘΗ ΕΠΑΝΑΛΗΨΗ: Επικαιροποίηση Δελτίου Τιμών ΜΗ.ΣΥ.ΦΑ. λόγω διοικητικών μεταβολών",
        ),
        (
            13661,
            "10/09/2025",
            "«Αναθεώρηση καταλόγου μη συνταγογραφούμενων φαρμάκων (ΜΗ.ΣΥ.ΦΑ.) για το έτος 2025»",
        ),
    ]

    def __init__(self):
        self.requests: list[str] = []

    def get(self, url: str):
        self.requests.append(url)
        if url.startswith(moh.OTC_BULLETINS + "?page="):
            text = "".join(
                f'<span class="datetime">{d}</span><h1><a href="{moh.OTC_BULLETINS}/{aid}-x">{t}</a></h1>'
                # an article of another section on the same page is ignored
                f'<span class="datetime">{d}</span><h1><a href="{moh.PRICE_BULLETINS}/9{aid}-y">x</a></h1>'
                for aid, d, t in self.LISTING
            )
            return SimpleNamespace(text=text, content=text.encode())
        if "?fdl=" in url:
            return SimpleNamespace(text="", content=b"file:" + url.encode())
        aid = int(url.rsplit("/", 1)[1].split("-", 1)[0])
        files = ["YA.pdf", "Πίνακας.pdf"] if aid == 13833 else ["YA.pdf", "Πίνακας.xlsx"]
        text = "".join(f'<a href="{url}?fdl={aid}{i}">{f} Μέγεθος: 1 MB</a>' for i, f in enumerate(files))
        return SimpleNamespace(text=text, content=text.encode())


def test_fetch_otc_section(tmp_path, monkeypatch):
    site = FakeOtcSite()
    monkeypatch.setattr(moh.Client, "get", lambda self, url: site.get(url))
    monkeypatch.setattr(sys, "argv", ["ingest.fetch", "--section", "otc", "--out", str(tmp_path)])
    fetch.main()
    saved = read_manifest(tmp_path / "manifest_otc.json")
    assert [(a.article_id, a.kind) for a in saved] == [
        (14636, "new"),
        (13833, "update"),
        (13661, "repricing"),
    ]
    assert sorted(p.name for p in (tmp_path / "raw").iterdir()) == [
        "moh_13661_136611.xlsx",
        "moh_13833_138330.pdf",  # no xlsx: its pdfs instead
        "moh_13833_138331.pdf",
        "moh_14636_146361.xlsx",
    ]
    site.requests.clear()  # second run: only the listing
    fetch.main()
    assert all("?page=" in u for u in site.requests)
