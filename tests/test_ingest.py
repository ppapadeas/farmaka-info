from pathlib import Path

import pytest

from ingest.build import SAMPLES, merge, validate
from ingest.moh import classify
from ingest.normalize import HEADER_MAP, _key, ean13_ok, read_bulletin, split_substances

RAW = Path(__file__).resolve().parent.parent / "samples" / "raw"


def test_header_variants_fold():
    assert HEADER_MAP[_key("ΟΝΟΝΑΣΙΑ ΚΑΚ")] == "mah"
    assert HEADER_MAP[_key("Περιγραφή Προϊόντος")] == "name"
    assert HEADER_MAP[_key("Δραστική(ές) Ουσία(ες)")] == "substance"  # moh article 12962
    assert HEADER_MAP[_key("ΤΙΜΗ ΠΑΡΑΓΩΓΟΥ")] == HEADER_MAP[_key("Τιμή Παραγωγού")]
    # older complete lists (moh articles 7309, 9022, 9794, 10267)
    assert HEADER_MAP[_key("ΜΗ ΑΠΟΖΗΜΙΟΥΜΕΝΑ")] == "non_reimbursed"
    assert HEADER_MAP[_key("ΑΠΟΖΗΜΙΟΥΜΕΝΑ")] == "note_reimbursed"
    assert HEADER_MAP[_key("Νέο")] == HEADER_MAP[_key("ΝΕΟ ΠΡΟΪΟΝ")] == "note_new"
    assert HEADER_MAP[_key("Επισήμανση νέων ΔΤΦ")] == "note_new"
    assert HEADER_MAP[_key("Επικαιροποίηση του σκευάσματος ως προς:")] == "note_change"


def test_substance_split():
    assert split_substances("CAPTOPRIL:HYDROCHLOROTHIAZIDE") == ["CAPTOPRIL", "HYDROCHLOROTHIAZIDE"]
    assert split_substances("LINAGLIPTIN,METFORMIN HYDROCHLORIDE") == [
        "LINAGLIPTIN",
        "METFORMIN HYDROCHLORIDE",
    ]
    assert split_substances(None) == []


def test_ean13():
    assert ean13_ok("2802100402016")
    assert not ean13_ok("2802100402017")


@pytest.mark.parametrize(
    "title,kind",
    [
        ("Ο.Ε. Επικαιροποίηση Δελτίου Τιμών Φαρμάκων με ενσωμάτωση Διοικητικών Μεταβολών", "full"),
        ("Δελτίο Τιμών Νέων Γενοσήμων Φαρμάκων Ανθρώπινης Χρήσης Αυγούστου 2026", "delta"),
        ("Συμπληρωματικό Δελτίο Τιμών Φαρμάκων Ανθρώπινης Χρήσης, κατόπιν αιτήματος μείωσης τιμής", "delta"),
        ("«Δελτίο Τιμών Νέων Τελικών Προϊόντων Φαρμακευτικής Κάνναβης»", "cannabis"),
        ("«Τροποποίηση της υπό στοιχεία Δ3(α) 21222/29-5-2026 Απόφασης»", "amendment"),
        ("ΔΕΛΤΙΟ ΤΙΜΩΝ ΝΕΩΝ ΓΕΝΟΣΗΜΩΝ ΦΑΡΜΑΚΩΝ ΑΝΘΡΩΠΙΝΗΣ ΧΡΗΣΗΣ ΑΠΡΙΛΙΟΥ 2020", "delta"),
        ("Δελτίο αναθεωρημένων τιμών φαρμάκων ανθρώπινης χρήσης, Δεκεμβρίου 2025", "revision"),
        ("Ο.Ε. «Τροποποίηση της υπό στοιχεία Δ3(α) 58275/29-12-2025 απόφασης", "amendment"),
        ("Ορθή Επανάληψη της Δ3(α)75126/30.12.2022 «Τροποποίηση…»", "amendment"),
        ("Ακύρωση της Δ3(α)7458/12-2-2019 απόφασης κατά το μέρος…", "amendment"),
    ],
)
def test_classify(title, kind):
    assert classify(title) == kind


def test_full_list_shape():
    rows = read_bulletin(RAW / "moh_14818_epikairopoihsh_2026-09.xlsx")
    assert len(rows) == 8917
    assert len({r.eof_code for r in rows}) == 8917
    assert all(r.barcode[3:12] == r.eof_code for r in rows)
    # Greek and Latin N both mean "non reimbursed"
    assert sum(r.non_reimbursed for r in rows) == 321


def test_merge_applies_deltas_in_order():
    sources = [(d, i, k, RAW / f) for d, i, k, f in SAMPLES]
    base, deltas, rows, history = merge(sources)
    assert base[1] == 14818 and [d[1] for d in deltas] == [14872, 14930]
    assert len(rows) == 8922  # 8917 + 5 new generics
    assert rows["312080101"].price_retail == 547.94  # XYDALBA price cut
    events = [h for h in history if h["eof_code"] == "312080101" and h["field"] == "price_retail"]
    assert events == [
        {
            "eof_code": "312080101",
            "field": "price_retail",
            "old": None,
            "new": 823.64,
            "change": "first_seen",  # the series starts at the oldest full list
            "published": "2026-10-05",
            "moh_article": 14818,
        },
        {
            "eof_code": "312080101",
            "field": "price_retail",
            "old": 823.64,
            "new": 547.94,
            "change": "update",
            "published": "2026-11-06",
            "moh_article": 14930,
        },
    ]
    first_seen = [h for h in history if h["change"] == "first_seen"]
    assert len({h["eof_code"] for h in first_seen}) == 8917 and len(first_seen) == 8917 * 4
    assert {h["moh_article"] for h in first_seen} == {14818}
    # the August generics are introduced by a delta: "new", as before
    new = [h for h in history if h["change"] == "new"]
    assert len({h["eof_code"] for h in new}) == 5 and {h["moh_article"] for h in new} == {14872}
    problems = validate(rows)
    assert not [p for p in problems if "EAN" in p or "embed" in p or "ordering" in p]


def test_parse_price_text_cells():
    from ingest.normalize import AmbiguousValue, parse_price

    assert parse_price(26.93) == 26.93
    assert parse_price("\n\n38,93") == 38.93  # seen in moh article 13611
    assert parse_price("1.234,56") == 1234.56
    with pytest.raises(AmbiguousValue):
        parse_price("17,07\n\n19,40")  # two values in one cell, moh article 14110


def test_parse_vat():
    from ingest.normalize import parse_vat

    assert parse_vat(0.06) == 0.06
    assert parse_vat("6%") == 0.06  # moh article 12962
    assert parse_vat(13) == 0.13
    assert parse_vat(None) == 0.0


def test_empty_code_taken_from_barcode(tmp_path):
    # moh article 9022 (06/2021): ADDAMEL N has an empty Κωδικός but a valid barcode
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(
        [
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
            "Νέο",
        ]
    )
    ws.append(
        [
            "",
            "2802021402010",
            "ADDAMEL N NEW C/S.SOL.IN BTx20 (AMP x10 ML)",
            "B05XA31",
            "",
            32.39,
            33.98,
            46.82,
            "ZINC CHLORIDE",
            "FRESENIUS KABI HELLAS AE",
            "6%",
            "",
        ]
    )
    ws.append(["", "", "", "", "", None, None, None, "", "", "", ""])  # blank row
    path = tmp_path / "moh_9022_test.xlsx"
    wb.save(path)
    issues: list[dict] = []
    rows = read_bulletin(path, issues)
    assert [r.eof_code for r in rows] == ["202140201"]
    assert issues and issues[0]["problem"].startswith("empty eof_code")
