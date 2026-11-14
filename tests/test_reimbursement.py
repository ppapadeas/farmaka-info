"""Reimbursement catalogue parser, on the two sample PDFs of MoH article 14875 (22/10/2026).

Expected values were read by eye from the pdfplumber text of the pages named in each test.
Each PDF is parsed once per module (about 40 s for the main list, 13 s for N3816).
"""

import csv
import re
from pathlib import Path

import pytest

from ingest import reimbursement
from ingest.reimbursement import (
    AmbiguousRow,
    CatalogueError,
    parse_number,
    read_catalogue,
    write_csv,
)

RAW = Path(__file__).resolve().parent.parent / "samples" / "raw"
MAIN_PDF = RAW / "katalogos_2026-10.pdf"
N3816_PDF = RAW / "katalogos_n3816_2026-10.pdf"

PPI = (
    "Περιορισμός:Αποζημιώνονται για τη Θεραπεία πεπτικού έλκους,γαστροοισοφαγικής παλινδρόμησης "
    "και φαρμακευτικής γαστροπροστασίας σε ασθενείς με αυξημένο κίνδυνο εκδήλωσης γαστροδωδ/κού "
    "έλκους.Ειδικότερα για την γαστροπροστασία σε χορήγηση κλοπιδογρέλης 1η επιλογή είναι η "
    "χορήγηση Η2 αναστολέων."
)


@pytest.fixture(scope="module")
def main_issues():
    issues: list[dict] = []
    rows = read_catalogue(MAIN_PDF, "main", ".", issues)
    return rows, issues


@pytest.fixture(scope="module")
def n3816_issues():
    issues: list[dict] = []
    rows = read_catalogue(N3816_PDF, "n3816", ",", issues)
    return rows, issues


@pytest.fixture(scope="module")
def main(main_issues):
    return {r.eof_code: r for r in main_issues[0]}


@pytest.fixture(scope="module")
def n3816(n3816_issues):
    return {r.eof_code: r for r in n3816_issues[0]}


def test_row_counts(main_issues, n3816_issues):
    main_rows, main_problems = main_issues
    n_rows, n_problems = n3816_issues
    assert (len(main_rows), len(n_rows)) == (5689, 1589)
    assert main_problems == [] and n_problems == []
    # 5,678 / 1,588 have a well-formed ATC; the rest carry placeholders such as
    # 'A12AX-1', 'D05AXxx', 'D05AX--' (main) and 'L01FFF0' (N3816) but are real packages.
    ok = re.compile(r"[A-Z]\d\d[A-Z]{2}\d\d")
    assert sum(bool(ok.fullmatch(r.atc)) for r in main_rows) == 5678
    assert sum(bool(ok.fullmatch(r.atc)) for r in n_rows) == 1588


def test_codes_unique_and_lists_disjoint(main_issues, n3816_issues, main, n3816):
    for rows, by_code in ((main_issues[0], main), (n3816_issues[0], n3816)):
        assert len(by_code) == len(rows)
        assert all(re.fullmatch(r"\d{9}", c) for c in by_code)
    assert not main.keys() & n3816.keys()
    assert {r.list_name for r in main.values()} == {"main"}
    assert {r.list_name for r in n3816.values()} == {"n3816"}


def test_main_spot_checks(main):
    # page 1
    r = main["140190101"]
    assert (r.atc, r.name_pdf, r.dose_form, r.strength, r.pack) == (
        "A02BA01",
        "TAGAMET",
        "F.C.TAB",
        "200MG/TAB",
        "ΒΤx50",
    )
    assert (r.dispensing, r.mah_pdf, r.flag_s, r.flag_t) == (
        "ΜΕ ΙΑΤΡΙΚΗ ΣΥΝΤΑΓΗ",
        "ΒΙΑΝΕΞ Α.Ε. ΑΝΩΝΥΜΟΣ",
        "",
        "Φ",
    )
    assert (r.ddd, r.ddd_unit, r.daily_doses_per_pack, r.daily_cost) == (0.8, "G", 12.5, 0.481)
    assert (r.retail_in_list, r.reimbursement_price, r.page) == (6.01, 4.94, 1)
    assert r.route_group == "Από του στόματος χορήγηση (στερεές μορφές)"
    assert r.therapeutic_group is None and r.restrictions == []
    assert main["140190401"].route_group == "Παρεντερική χορήγηση (ένεση ή έγχυση)"
    # truncated name runs straight into the next column; split by position
    assert (main["233630201"].name_pdf, main["233630201"].dose_form) == ("FAMOTIDINE/ΒΕΛΚ", "F.C.TAB")
    # page 30: ΛΙΑΝΙΚΗ prints thousands with a comma ('2,075.63'), ΑΠΟΖΗΜ. without ('2075.63')
    r = main["248580201"]
    assert (r.name_pdf, r.flag_t, r.retail_in_list, r.reimbursement_price) == (
        "CEPROTIN",
        "Ν",
        2075.63,
        2075.63,
    )
    assert (r.ddd, r.ddd_unit, r.daily_doses_per_pack, r.daily_cost) == (500.0, "IU", 2.0, 1037.815)
    # page 110: DDD below print precision shows as '0.000 G'; ΑΗΔ/ΚΗΘ are still given
    r = main["273260212"]
    assert (r.name_pdf, r.ddd, r.ddd_unit, r.daily_doses_per_pack, r.daily_cost) == (
        "EUTHYROX",
        None,
        "G",
        10.0,
        0.089,
    )
    # page 91: placeholder ATC, kept as printed
    r = main["268680101"]
    assert (r.atc, r.name_pdf, r.ddd, r.ddd_unit, r.retail_in_list) == ("D05AXxx", "ASALID", 1.0, "ML", 5.28)
    assert r.therapeutic_group == "G01 Λοιπά αντιψωριασικά για τοπική χρήση, αμιγή"


def test_n3816_greek_numbers(n3816):
    # page 1: '4.865,32', no DDD ('0,000', empty unit, '0,00', '0,000')
    r = n3816["339420101"]
    assert (r.name_pdf, r.retail_in_list, r.reimbursement_price) == ("IQIRVO", 4865.32, 4865.32)
    assert (r.ddd, r.ddd_unit, r.daily_doses_per_pack, r.daily_cost) == (None, None, None, None)
    # page 2: thousands in ΚΗΘ ('1.122,260')
    r = n3816["269070101"]
    assert (r.name_pdf, r.flag_t, r.ddd, r.ddd_unit) == ("ALDURAZYME", "Ν", 1.0, "TU")
    assert (r.daily_doses_per_pack, r.daily_cost, r.retail_in_list) == (0.5, 1122.26, 561.13)
    assert r.therapeutic_group == "G03 Ένζυμα για τη Βλεννοπολυσακχαρίδωση τύπου Ι"
    # page 69: two thousands separators ('1.716.882,33')
    assert n3816["325190130"].retail_in_list == 1716882.33
    # page 1: group heading and parenteral route
    r = n3816["234950201"]
    assert (r.name_pdf, r.ddd, r.ddd_unit, r.daily_doses_per_pack, r.daily_cost) == (
        "CEREZYME",
        300.0,
        "U",
        1.33,
        933.805,
    )
    assert r.therapeutic_group == "G01 Ένζυμα για τη νόσο του Gaucher τύπου Ι και ΙΙΙ"
    assert r.route_group == "Παρεντερική χορήγηση (ένεση ή έγχυση)"
    # every N3816 package is reimbursed at its full retail price
    assert all(r.reimbursement_price == r.retail_in_list for r in n3816.values())


def test_daily_cost_is_retail_over_daily_doses(main, n3816):
    checked = 0
    for r in [*main.values(), *n3816.values()]:
        assert r.reimbursement_price > 0
        assert r.reimbursement_price <= r.retail_in_list
        if r.daily_doses_per_pack and r.daily_cost:
            ahd = r.daily_doses_per_pack
            # ΚΗΘ printed to 3 decimals, ΑΗΔ to 2
            slack = 0.0005 + r.retail_in_list * 0.005 / (ahd * (ahd - 0.005))
            assert abs(r.retail_in_list / ahd - r.daily_cost) <= slack, r
            checked += 1
        else:
            assert r.daily_doses_per_pack is None and r.daily_cost is None
    assert checked == 5092 + 1120


def test_restrictions_follow_their_heading(main, n3816):
    # ATC4 note (A02BC, page 1) covers every A02BC row, in every route group
    a02bc = [r for r in main.values() if r.atc.startswith("A02BC")]
    assert len({r.route_group for r in a02bc}) > 1
    assert all(r.restrictions[0] == PPI for r in a02bc)
    assert main["273790204"].restrictions == [PPI]  # ALEVIOR
    assert main["140190101"].restrictions == []  # TAGAMET, A02BA
    # ATC4 note plus ATC5 note (page 3)
    assert main["336210101"].restrictions == [PPI, "Περιορισμός: Δεν αποζημιώνεται σε ενήλικες."]
    # N3816 page 1: ATC5 note under BUDESONIDE / solid forms, not the other route group
    jorveza = ["Περιορισμός: Χορήγηση ως θεραπεία συντήρησης για χρονικό διάστημα έως 48 εβδομάδες."]
    assert n3816["321940203"].restrictions == jorveza
    assert n3816["321940104"].restrictions == jorveza
    assert n3816["332030101"].restrictions == []  # KINPEYGO, same ATC5, other route
    assert n3816["302470104"].restrictions == [
        "Περιορισμός: Αποζημιώνεται μόνο επί θετικής καλλιέργειας και επί ανίχνευσης τοξινών C. difficile"
    ]
    assert sum(bool(r.restrictions) for r in main.values()) == 874
    assert sum(bool(r.restrictions) for r in n3816.values()) == 176


def test_flags_and_routes(main, n3816):
    rows = [*main.values(), *n3816.values()]
    assert {r.flag_s for r in rows} == {""}  # Σ is empty in both files
    assert {r.flag_t for r in rows} == {"", "Φ", "Ν", "N", "(Φ)", "(Ν)"}
    assert all(r.route_group for r in rows)


def test_parse_number_is_explicit():
    assert parse_number("6.01", ".") == 6.01
    assert parse_number("2,075.63", ".") == 2075.63
    assert parse_number("4.865,32", ",") == 4865.32
    assert parse_number("1.716.882,33", ",") == 1716882.33
    assert parse_number("0,000", ",") == 0.0
    for text, decimal in [("6.01", ","), ("4.865,32", "."), ("1.234", ","), ("12", "."), ("1,23.45", ".")]:
        with pytest.raises(CatalogueError):
            parse_number(text, decimal)
    with pytest.raises(ValueError):
        parse_number("6.01", "")


def test_wrong_decimal_for_file_raises():
    with pytest.raises(CatalogueError, match="does not match decimal separator"):
        read_catalogue(N3816_PDF, "n3816", ".")
    with pytest.raises(CatalogueError, match="does not match decimal separator"):
        read_catalogue(MAIN_PDF, "main", ",")


def test_wrong_list_name_raises():
    with pytest.raises(CatalogueError, match="does not match list"):
        read_catalogue(MAIN_PDF, "n3816", ".")
    with pytest.raises(ValueError):
        read_catalogue(MAIN_PDF, "other", ".")


def test_write_csv(main, tmp_path):
    rows = [main["140190101"], main["336210101"]]
    out = tmp_path / "reimbursement_main.csv"
    write_csv(rows, out, MAIN_PDF.name, "14875", "2026-10-22")
    with out.open(encoding="utf-8", newline="") as fh:
        got = list(csv.DictReader(fh))
    assert list(got[0]) == reimbursement.CSV_FIELDS
    assert (got[0]["eof_code"], got[0]["retail_in_list"], got[0]["restrictions"]) == ("140190101", "6.01", "")
    assert got[1]["restrictions"] == PPI + " || Περιορισμός: Δεν αποζημιώνεται σε ενήλικες."
    assert (got[1]["source_file"], got[1]["moh_article"], got[1]["published"]) == (
        MAIN_PDF.name,
        "14875",
        "2026-10-22",
    )


# --- synthetic pages: state machine and issue handling without a PDF ---------------------

_BLUE = (0.3608, 0.7647, 0.9804)
_W = 4.0  # character width of the synthetic font


def _chars(text, x, top, bold=True, colour=(0.0, 0.0, 0.0), size=6.0):
    font = "X+DejaVuSans-Bold" if bold else "X+DejaVuSans"
    return [
        {
            "text": ch,
            "x0": x + i * _W,
            "x1": x + (i + 1) * _W,
            "top": top,
            "size": size,
            "fontname": font,
            "non_stroking_color": colour,
        }
        for i, ch in enumerate(text)
    ]


def _page(body):
    chars = _chars("ΚΑΤΑΛΟΓΟΣ", 46, 40, size=11.0, colour=(0.04, 0.38, 0.69))
    for top, titles in zip((94.0, 97.5, 101.0), reimbursement._HEADER):
        for x, title in titles:
            chars += _chars(title, x, top)
    top = 120.0
    for item in body:
        top += 12.0
        chars += item(top)
    return chars


def _heading(text, x):
    return lambda top: _chars(text, x, top)


def _note(text, x):
    return lambda top: _chars(text, x, top, colour=_BLUE)


def _pkg(code, khth="0.481"):
    values = ["0.800", "G", "12.50", khth, "6.01", "4.94"]
    starts = [593.0, 617.0, 641.0, 681.0, 729.0, 779.0]

    def make(top):
        out = _chars("A02BA01", 36, top) + _chars(code, 76, top, bold=False)
        out += _chars("TAGAMET", 132, top, bold=False)
        for v, x in zip(values, starts):
            out += _chars(v, x, top, bold=False)
        return out

    return make


def _run(monkeypatch, body, issues=None):
    chars = _page(body)
    monkeypatch.setattr(reimbursement, "_page_lines", lambda path: iter([(1, reimbursement._lines(chars))]))
    return read_catalogue(Path("synthetic.pdf"), "main", ".", issues)


def test_synthetic_scoping_and_issues(monkeypatch):
    body = [
        _heading("A02 -ATC2", 24),
        _heading("A02B -ATC3", 28),
        _heading("A02BA -ATC4", 32),
        _note("Περιορισμός: R4", 40),
        _heading("A02BA01 CIMETIDINE", 36),
        _note("Περιορισμός: R5", 36),
        _pkg("140190101"),
        _pkg("140190102", khth="0.999"),  # contradicts ΛΙΑΝΙΚΗ / ΑΗΔ
        _heading("A02BA01 CIMETIDINE", 36),
        _pkg("140190103"),
    ]
    issues: list[dict] = []
    rows = _run(monkeypatch, body, issues)
    assert [(r.eof_code, r.restrictions) for r in rows] == [
        ("140190101", ["Περιορισμός: R4", "Περιορισμός: R5"]),
        ("140190103", ["Περιορισμός: R4"]),
    ]
    assert [(i["eof_code"], i["page"]) for i in issues] == [("140190102", 1)]
    with pytest.raises(AmbiguousRow, match="ΚΗΘ"):
        _run(monkeypatch, body)


def test_synthetic_layout_errors(monkeypatch):
    with pytest.raises(CatalogueError, match="after a package row"):
        _run(monkeypatch, [_heading("A02BA01 X", 36), _pkg("140190101"), _note("Περιορισμός: ?", 36)])
    with pytest.raises(CatalogueError, match="restriction at x=36.0 after atc4"):
        _run(monkeypatch, [_heading("A02BA -X", 32), _note("Περιορισμός: ?", 36)])
    with pytest.raises(CatalogueError, match="already on page"):
        _run(monkeypatch, [_heading("A02BA01 X", 36), _pkg("140190101"), _pkg("140190101")])
    with pytest.raises(CatalogueError, match="not 9 digits"):
        _run(monkeypatch, [_heading("A02BA01 X", 36), _pkg("14019010")])
    with pytest.raises(CatalogueError, match="unknown heading"):
        _run(monkeypatch, [_heading("A02BA01 X", 44)])
