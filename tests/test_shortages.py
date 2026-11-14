"""Medicines in shortage: EOF lists and notices, EMA table, matching, fetch, build.

No network. samples/shortages/ holds trimmed copies of real EOF pages (14/11/2026) and
the tables pdfplumber extracts from the two EOF list PDFs (trimmed; the PDFs embed fonts
and weigh > 1 MB). The export-ban table, the EMA table and our package lists are
synthetic, written here in the layout of the real files.
"""

import csv
import json
from datetime import date
from pathlib import Path

import openpyxl
import pytest

from ingest import shortages as sh

SAMPLES = Path(__file__).resolve().parent.parent / "samples" / "shortages"
TODAY = date(2026, 11, 14)

PACKAGES = [
    # eof_code, name, substances
    ("192850101", "CYTOTEC TABLET 200MCG/TAB ΒΤx42 (BLIST 3x14)", "MISOPROSTOL"),
    ("326380102", "TEGSEDI INJ.SOL 284MG 4 PF SYRS", "INOTERSEN"),
    ("264660301", "EMEND CAPS 125MG/CAP BTx3 1 cap x125MG + 2 caps x80MG", "APREPITANT"),
    ("281020103", "OROPERIDYS OR.DISP.TA 10MG/TAB BTx21 TABS", "DOMPERIDONE"),
    ("308100101", "BRONCHITOL INHPD.CAP 40MG/CAP BTx10", "MANNITOL"),
    ("308100102", "BRONCHITOL INHPD.CAP 40MG/CAP BTx280", "MANNITOL"),
    ("310000101", "QUETIAPINE F.C.TAB 25MG/TAB BTx30", "QUETIAPINE"),
]
OTC = [
    ("023280101", "DEPON TAB 500MG/TAB BTx20(BLIST2x10)", "PARACETAMOL"),
    ("023280202", "DEPON EF.TAB 500MG/TAB ΒΤ x 10", "PARACETAMOL"),
    ("023280603", "DEPON SYR 120MG/5ML FLx150ML", "PARACETAMOL"),
    ("023280605", "DEPON SYR 120MG/5ML FLx150ML", "PARACETAMOL"),
]


def _write_packages(path: Path, rows) -> Path:
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["eof_code", "barcode", "name", "substances"])
        for code, name, subst in rows:
            w.writerow([code, "", name, subst])
    return path


@pytest.fixture
def catalogue(tmp_path):
    return sh.Catalogue.read(
        _write_packages(tmp_path / "packages.csv", PACKAGES), _write_packages(tmp_path / "otc.csv", OTC)
    )


def _tables(name: str) -> list:
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))["tables"]


# --- dates, titles, codes ----------------------------------------------------------


def test_greek_dates():
    assert sh.greek_date("ΛΙΣΤΑ … 31 ΟΚΤΩΒΡΙΟΥ 2026 ΟΡΘΗ ΕΠΑΝΑΛΗΨΗ") == date(2026, 10, 31)
    assert sh.greek_date("31 ΟΚΤΩΜΒΡΙΟΥ 2025") == date(2025, 10, 31)  # misspelt in a real title
    assert sh.greek_date("10 Νοέ 2026") == date(2026, 11, 10)
    assert sh.greek_date("28 Ιούλ 2026") == date(2026, 7, 28)
    assert sh.greek_date("ΠΕΡΙΟΡΙΣΜΕΝΗΣ ΔΙΑΘΕΣΙΜΟΤΗΤΑΣ 28/03/2025") == date(2025, 3, 28)
    assert sh.greek_date("ΔΙΑΘΕΣΙΜΟΤΗΤΑΣ 31 ΜΑΪΟΥ", year=2026) == date(2026, 5, 31)
    assert sh.greek_date("Δήλωση Αποθεμάτων Φαρμάκων") is None
    assert sh.iso_date("22/10/26") == "2026-10-22"
    assert sh.iso_date(" ") == ""
    with pytest.raises(sh.ShortageError):
        sh.iso_date("ΝΟΕΜΒΡΙΟΥ")


@pytest.mark.parametrize(
    "title, kind",
    [
        (
            "ΛΙΣΤΑ ΦΑΡΜΑΚΕΥΤΙΚΩΝ ΣΚΕΥΑΣΜΑΤΩΝ ΠΕΡΙΟΡΙΣΜΕΝΗΣ ΔΙΑΘΕΣΙΜΟΤΗΤΑΣ 31 ΟΚΤΩΒΡΙΟΥ 2026",
            "limited_availability",
        ),
        (
            "ΑΝΑΡΤΗΣΗ ΛΙΣΤΑ ΦΑΡΜΑΚΕΥΤΙΚΩΝ ΣΚΕΥΑΣΜΑΤΩΝ ΠΕΡΙΟΡΙΣΜΕΝΗΣ ΔΙΑΘΕΣΙΜΟΤΗΤΑΣ 30 ΙΟΥΝΙΟΥ 2026",
            "limited_availability",
        ),
        (
            "ΛΙΣΤΑ ΦΑΡΜΑΚΕΥΤΙΚΩΝ ΣΚΕΥΑΣΜΑΤΩΝ ΟΡΙΣΤΙΚΗΣ ΔΙΑΚΟΠΗΣ ΚΥΚΛΟΦΟΡΙΑΣ 31 ΔΕΚΕΜΒΡΙΟΥ 2025",
            "discontinuation_list",
        ),
        ("ΠΡΟΣΩΡΙΝΗ ΑΠΑΓΟΡΕΥΣΗ ΠΑΡΑΛΛΗΛΩΝ ΕΞΑΓΩΓΩΝ ΚΑΙ ΕΝΔΟΚΟΙΝΟΤΙΚΗΣ ,ΔΙΑΚΙΝΗΣΗΣ", "export_ban"),
        (
            "Ενημέρωση για την προγραμματισμένη οριστική διακοπή κυκλοφορίας του EMEND 125 mg",
            "discontinuation",
        ),
        (
            "Επικοινωνία σχετικά με την οριστική διακοπή κυκλοφορίας επιλεγμένων σκευασμάτων",
            "discontinuation",
        ),
        ("ΕΠΙΚΟΙΝΩΝΙΑ ΓΙΑ ΕΛΛΕΙΨΗ ΦΑΡΜΑΚΕΥΤΙΚΟΥ ΠΡΟΪΟΝΤΟΣ NATPAR", "shortage"),
        ("Επικοινωνία για την έλλειψη του φαρμακευτικού προϊόντος Holoxan", "shortage"),
        ("Δήλωση Αποθεμάτων Φαρμάκων Ανθρώπινης Χρήσης", "other"),
        ("Επάρκεια φαρμάκων που περιέχουν τριμεθοπρίμη και σουλφομεθοξαζόλη", "other"),
    ],
)
def test_classify(title, kind):
    assert sh.classify(title) == kind


def test_barcode_to_eof_code():
    assert sh.ean13_ok("2801928501017")
    assert sh.eof_code_from_barcode("2801928501017") == "192850101"  # positions 4-12
    assert sh.eof_code_from_barcode("2809001101017") == "900110101"  # Ι.Φ.Ε.Τ. import, not ours
    assert sh.eof_code_from_barcode("2801928501018") is None  # wrong check digit
    assert sh.eof_code_from_barcode("5201928501013") is None  # not a Greek 280 code
    assert sh.eof_code_from_barcode("192850101") is None


# --- EOF pages ------------------------------------------------------------------------


def test_parse_listing():
    posts, pages = sh.parse_listing((SAMPLES / "eof_listing_page1.html").read_text(encoding="utf-8"))
    assert pages == [2, 3, 7]
    by_id = {p.post_id: p for p in posts}
    assert [p.post_id for p in posts] == [10547, 10531, 10524, 10411, 10287, 9961]
    assert by_id[10531].kind == "limited_availability"
    assert by_id[10531].published == date(2026, 11, 9)
    assert by_id[10531].list_date == date(2026, 10, 31)
    assert by_id[10287].list_date == date(2026, 10, 31)  # posted 02/10, title changed later
    assert by_id[10287].modified.startswith("2026-11-09")
    assert by_id[9961].kind == "export_ban"
    assert by_id[10411].kind == "other"
    assert by_id[10547].url.startswith("https://www.eof.gr/%CE%B5%CE%BD")  # one spelling


def test_parse_post_attachments_only_from_the_post():
    body, files = sh.parse_post(
        (SAMPLES / "eof_post_list_2026-10-31.html").read_text(encoding="utf-8"), "https://www.eof.gr/x/"
    )
    assert "31 ΟΚΤΩΒΡΙΟΥ 2026" in body
    assert len(files) == 1  # the sidebar link is not part of the post
    assert files[0].startswith("https://www.eof.gr/wp-content/uploads/2026/11/%CE%9B%CE%99%CE%A3")
    assert files[0].endswith(
        "-2026-%CE%9F%CE%A1%CE%98%CE%97-%CE%95%CE%A0%CE%91%CE%9D%CE%91%CE%9B%CE%97%CE%A8%CE%97.pdf"
    )
    assert sh.canonical_url("https://www.eof.gr/wp-content/uploads/2026/11/ΛΙΣΤΑ.pdf") == sh.canonical_url(
        "https://www.eof.gr/wp-content/uploads/2026/11/%ce%9b%ce%99%ce%a3%ce%a4%ce%91.pdf"
    )


def test_newest_list_selection():
    def post(pid, title, published, kind=None):
        return sh.Post(pid, f"https://www.eof.gr/{pid}/", title, published, "", kind or sh.classify(title))

    la = "ΛΙΣΤΑ ΦΑΡΜΑΚΕΥΤΙΚΩΝ ΣΚΕΥΑΣΜΑΤΩΝ ΠΕΡΙΟΡΙΣΜΕΝΗΣ ΔΙΑΘΕΣΙΜΟΤΗΤΑΣ "
    posts = [
        post(1, la + "31 ΜΑΪΟΥ", date(2026, 6, 9)),  # year from the post date
        post(2, la + "31 ΟΚΤΩΒΡΙΟΥ 2026", date(2026, 10, 2)),
        post(3, la + "31 ΟΚΤΩΒΡΙΟΥ 2026 ΟΡΘΗ ΕΠΑΝΑΛΗΨΗ", date(2026, 11, 9)),
        post(4, la + "31 ΑΥΓΟΥΣΤΟΥ 2026", date(2026, 11, 10)),  # posted late, older list
        post(5, "Ενημέρωση για την προγραμματισμένη διακοπή κυκλοφορίας X", date(2026, 3, 1)),
        post(6, "Ενημέρωση για την προγραμματισμένη διακοπή κυκλοφορίας Y", date(2025, 3, 1)),
        post(7, "ΕΠΙΚΟΙΝΩΝΙΑ ΓΙΑ ΕΛΛΕΙΨΗ Z", date(2026, 3, 1)),
    ]
    assert posts[0].list_date == date(2026, 5, 31)
    chosen = [p.post_id for p in sh.current_posts(posts, TODAY)]
    assert chosen == [3, 5]  # the correction wins; notices: 365 days, shortages: 180 days


# --- EOF list tables ----------------------------------------------------------------


def test_limited_availability_tables():
    rows = sh.parse_tables(_tables("limited_availability_2026-10-31_tables.json"))
    assert len(rows) == 7
    main, emergency = rows[0].section, rows[-1].section
    assert main == "ΛΙΣΤΑ ΦΑΡΜΑΚΕΥΤΙΚΩΝ ΣΚΕΥΑΣΜΑΤΩΝ ΠΕΡΙΟΡΙΣΜΕΝΗΣ ΔΙΑΘΕΣΙΜΟΤΗΤΑΣ 31 ΟΚΤΩΒΡΙΟΥ 2026"
    assert emergency == "ΛΙΣΤΑ ΣΚΕΥΑΣΜΑΤΩΝ ΠΕΡΙΟΡΙΣΜΕΝΗΣ ΔΙΑΘΕΣΙΜΟΤΗΤΑΣ ΕΚΤΑΚΤΗΣ ΚΑΛΥΨΗΣ"
    assert [r.section for r in rows].count(main) == 5  # the header carries over to page 11
    cytotec = rows[0].fields
    assert cytotec["barcode"] == "2801928501017"
    assert cytotec["start"] == "22/10/26" and cytotec["end"] == "30/11/26"
    assert cytotec["reason"] == "Αυξημένη ζήτηση"
    assert cytotec["alternatives"].startswith("Εναλλακτικά άλλα σκευάσματα αναστολείς")
    assert rows[2].fields["substance"] == "ONDANSETRON HYDROCHLORIDE"  # cell line breaks joined
    pylera = rows[5].fields
    assert "alternatives" not in pylera and pylera["mah"] == "Ι.Φ.Ε.Τ." and pylera["end"] == ""


def test_discontinuation_tables():
    rows = sh.parse_tables(_tables("discontinuation_2025-12-31_tables.json"))
    assert len(rows) == 4
    assert rows[0].fields["discontinued"] == "28/11/25"
    assert rows[0].fields["name"] == "ABASAGLAR INJ.SOL 100U/ML BTX10 CARTRIDGES X 3ML"
    # the same package twice, two dates: both kept
    assert [r.fields["discontinued"] for r in rows[2:]] == ["29/09/25", "16/10/25"]


def test_export_ban_annex_layout():
    """Synthetic: the columns of the annex of the (scanned) export-ban decisions."""
    header = ["Α/Α", "Barcode", "Περιγραφή Προϊόντος", "Κάτοχος άδειας κυκλοφορίας (ΚΑΚ)", "Κωδικός ATC"]
    header.append("Δραστική(ές) ουσία(ες)")
    table = [["Πίνακας 1", None, None, None, None, None], header]
    table.append(["1", "2801928501017", "CYTOTEC TABLET 200MCG/TAB", "PFIZER", "A02BB01", "MISOPROSTOL"])
    rows = sh.parse_tables([table])
    assert rows[0].fields["barcode"] == "2801928501017" and rows[0].fields["seq"] == "1"


def test_unknown_header_and_stray_rows_fail_loudly():
    with pytest.raises(sh.UnknownHeader):
        sh.parse_tables([[["Barcode", "Περιγραφή", "Νέα στήλη"], ["2801928501017", "X", "Y"]]])
    with pytest.raises(sh.ShortageError):
        sh.parse_tables([[["2801928501017", "CYTOTEC", "A02BB01"]]])  # no header yet
    with pytest.raises(sh.ShortageError):
        sh.parse_tables([[["Barcode", "Περιγραφή"], ["2801928501017", "X", "extra"]]])


def test_scanned_pdf_has_no_rows(tmp_path):
    pdfium = pytest.importorskip("pypdfium2")
    doc = pdfium.PdfDocument.new()
    doc.new_page(595, 842)
    doc.save(tmp_path / "scan.pdf")
    with pytest.raises(sh.ScannedDocument):
        sh.pdf_tables(tmp_path / "scan.pdf")


# --- matching by name -------------------------------------------------------------


def test_strengths():
    assert sh.text_strengths("TEGSEDI® (ινοτερσένη νατριούχος, 284 mg ενέσιμο διάλυμα)") == ["284MG"]
    assert sh.text_strengths("Holoxan (ιφωσφαμίδη, 500 mg, 1 g και 2 g κόνις)") == ["500MG", "1G", "2G"]
    assert sh.text_strengths("80 mg ;125 mg") == ["80MG", "125MG"]
    assert sh.text_strengths("1,0 μg") == ["1MCG"]
    assert sh.text_strengths("10,000 units") == []  # never guessed
    assert sh.strength_key("ONDANSETRON/KABI INJ.SOL 2MG/ML BT x 1 AMP x 2 ML") == "2MG"
    assert sh.strength_key("ABSEAMED INJ.SO.PFS 8000IU/0,8ML PF.SYR BTx 6") == "8000IU"


def test_true_match_by_brand_and_strength(catalogue):
    found = sh.products_in_text(catalogue, "ΕΠΙΚΟΙΝΩΝΙΑ ΓΙΑ ΕΛΛΕΙΨΗ ΦΑΡΜΑΚΕΥΤΙΚΟΥ ΠΡΟΪΟΝΤΟΣ ΤEGSEDI 284 mg")
    assert found == [("TEGSEDI", "ΤEGSEDI", ["284MG"])]  # Greek capital T in the title
    m = sh.match_brand_strength(catalogue, "TEGSEDI", "284MG")
    assert (m.codes, m.matched_by) == (["326380102"], "brand+strength")
    syrup = sh.match_brand_strength(catalogue, "DEPON", "120MG")  # two packages, one product
    assert syrup.codes == ["023280603", "023280605"]
    two = sh.products_in_text(catalogue, "EMEND 125 mg σκληρά καψάκια & EMEND 80 mg σκληρά καψάκια")
    assert [(b, s) for b, _, s in two] == [("EMEND", ["125MG"]), ("EMEND", ["80MG"])]
    assert (
        sh.match_brand_strength(catalogue, "EMEND", "80MG").problem == "no EMEND package with strength 80MG"
    )


def test_ambiguous_match_is_rejected(catalogue):
    m = sh.match_brand_strength(catalogue, "DEPON", "500MG")  # tablets and effervescent tablets
    assert m.codes == [] and m.problem.startswith("ambiguous")
    assert m.candidates == ["023280101", "023280202"]
    assert sh.match_brand_strength(catalogue, "TEGSEDI", None).problem == "no strength"


def test_substance_names_and_greek_words_are_not_brands(catalogue):
    assert sh.products_in_text(catalogue, "Quetiapine 25 mg") == []  # an INN, not a brand
    cat = sh.Catalogue()
    cat.add("999990101", "ΤΗΝ TAB 5MG/TAB BTx10", "manual")
    assert sh.products_in_text(cat, "για την έλλειψη ΤΗΝ 5 mg") == []  # no Latin letter


def test_list_row_without_barcode_matches_by_name(catalogue):
    exact = sh.match_package_name(catalogue, "CYTOTEC TABLET 200MCG/TAB ΒΤx42 (BLIST 3x14)")
    assert (exact.codes, exact.matched_by) == (["192850101"], "name")
    other_pack = sh.match_package_name(catalogue, "CYTOTEC TABLET 200MCG/TAB BTx20")
    assert (other_pack.codes, other_pack.matched_by) == (["192850101"], "brand+strength")


# --- EMA ------------------------------------------------------------------------------

EMA_HEADER = list(sh._EMA_COLUMNS)


def _ema_xlsx(path: Path, rows: list[dict]) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Shortage"
    ws.append(
        ["Content type:", "Shortage", "Output automatically generated from content on", "14/11/2026 - 06:00"]
    )
    for _ in range(7):
        ws.append([])
    ws.append(EMA_HEADER)
    for r in rows:
        ws.append([r.get(c, None) for c in EMA_HEADER])
    wb.save(path)
    return path


EMA_ROWS = [
    {
        "Category": "Human",
        "Medicine affected": "Tegsedi",
        "Supply shortage status": "Shortage ongoing",
        "Start of shortage date": "01/03/2027",
        "Last updated date": "12/11/2026",
        "Shortage URL": "https://www.ema.europa.eu/en/medicines/human/shortages/tegsedi",
    },
    {
        "Category": "Human",
        "Medicine affected": "Bronchitol",
        "Supply shortage status": "Shortage ongoing",
        "Strengths affected": "40 mg",
        "Expected resolution": "March 2027",
        "First published date": "14/11/2025",
        "Shortage URL": "https://www.ema.europa.eu/en/medicines/human/shortages/bronchitol",
    },
    {
        "Category": "Human",
        "Medicine affected": "Human normal immunoglobulins",
        "Supply shortage status": "Shortage ongoing",
        "Shortage URL": "https://www.ema.europa.eu/en/medicines/human/shortages/human-normal-immunoglobulins",
    },
    {
        "Category": "Human",
        "Medicine affected": "Dynastat",
        "Supply shortage status": "Shortage resolved",
        "Shortage URL": "https://www.ema.europa.eu/en/medicines/human/shortages/dynastat",
    },
]


def test_ema_table(tmp_path, catalogue):
    generated, recs = sh.read_ema_shortages(_ema_xlsx(tmp_path / "ema.xlsx", EMA_ROWS))
    assert generated == "14/11/2026 - 06:00" and len(recs) == 4
    links = {"TEGSEDI": ["326380102"]}  # from ema_links.csv
    m = sh.match_ema(catalogue, links, recs[0])
    assert (m.codes, m.matched_by) == (["326380102"], "ema_link")
    m = sh.match_ema(catalogue, links, recs[1])  # not centrally linked: brand + strength
    assert (m.codes, m.matched_by) == (["308100101", "308100102"], "brand+strength")
    assert sh.match_ema(catalogue, links, recs[2]).codes == []
    bad = dict(EMA_ROWS[0], **{"Supply shortage status": "Shortage maybe"})
    with pytest.raises(sh.ShortageError):
        sh.read_ema_shortages(_ema_xlsx(tmp_path / "bad.xlsx", [bad]))


# --- client and fetch -----------------------------------------------------------------


class FakeResponse:
    def __init__(self, status: int, content: bytes, headers: dict | None = None):
        self.status_code, self.content, self.headers = status, content, headers or {}


class FakeSession:
    """Serves samples by URL; records every request."""

    def __init__(self, routes):
        self.routes, self.headers, self.calls = routes, {}, []

    def get(self, url, timeout, allow_redirects):
        assert allow_redirects is False
        self.calls.append(url)
        for pattern, response in self.routes:
            if pattern(url):
                return response
        return FakeResponse(404, b"")


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    monkeypatch.setattr(sh, "MIN_INTERVAL", 0.0)


def test_client_is_polite_and_never_fetches_twice(tmp_path):
    session = FakeSession([(lambda u: u.endswith(".pdf"), FakeResponse(200, b"%PDF-1.4 x"))])
    client = sh.Client(sh.Cache(tmp_path), max_requests=2, session=session)
    assert session.headers["User-Agent"] == sh.USER_AGENT
    url = "https://www.eof.gr/wp-content/uploads/2026/11/ΛΙΣΤΑ.pdf"
    path = client.get(url)
    assert client.get(url) == path and len(session.calls) == 1
    entry = sh.Cache(tmp_path).latest(url)
    assert entry["sha256"] == sh._sha256(b"%PDF-1.4 x") and path.read_bytes() == b"%PDF-1.4 x"
    log = (tmp_path / "requests.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(log[0])["status"] == 200


def test_client_stops_on_refusal_and_budget(tmp_path):
    session = FakeSession([(lambda u: "busy" in u, FakeResponse(429, b"slow down"))])
    client = sh.Client(sh.Cache(tmp_path), max_requests=2, session=session)
    with pytest.raises(sh.Blocked):
        client.get("https://www.eof.gr/busy/")
    assert len(session.calls) == 1  # no retry
    with pytest.raises(sh.Blocked):
        client.get("https://www.eof.gr/missing.pdf")
    with pytest.raises(sh.BudgetExhausted):
        client.get("https://www.eof.gr/third/")
    assert len(session.calls) == 2
    html = FakeSession([(lambda u: True, FakeResponse(200, b"<html>blocked</html>"))])
    with pytest.raises(sh.ShortageError):  # a page where a PDF should be
        sh.Client(sh.Cache(tmp_path / "b"), 5, html).get("https://www.eof.gr/x.pdf")


def _routes():
    listing = (SAMPLES / "eof_listing_page1.html").read_bytes()
    list_post = (SAMPLES / "eof_post_list_2026-10-31.html").read_bytes()
    notice = (SAMPLES / "eof_post_shortage_tegsedi.html").read_bytes()
    posts, _ = sh.parse_listing(listing.decode("utf-8"))
    kinds = {p.url: p.kind for p in posts}
    return [
        (lambda u: u == sh.LISTING, FakeResponse(200, listing)),
        (lambda u: u.endswith(".pdf"), FakeResponse(200, b"%PDF-1.4 x")),
        (lambda u: kinds.get(u) in sh.LIST_KINDS, FakeResponse(200, list_post)),
        (lambda u: u in kinds, FakeResponse(200, notice)),
    ]


def test_fetch_is_incremental(tmp_path):
    session = FakeSession(_routes())
    client = sh.Client(sh.Cache(tmp_path), max_requests=20, session=session)
    sh.fetch(client, TODAY, ema=False)
    # listing; limited-availability post + its PDF; export-ban post (same PDF, cached);
    # two discontinuation notices of the last 365 days. Not the stock declaration.
    assert len(session.calls) == 6
    assert session.calls[0] == sh.LISTING
    again = FakeSession(_routes())
    sh.fetch(sh.Client(sh.Cache(tmp_path), max_requests=20, session=again), TODAY, ema=False)
    assert again.calls == []  # listing checked less than 6 hours ago, nothing changed
    with pytest.raises(sh.BudgetExhausted):
        sh.fetch(sh.Client(sh.Cache(tmp_path / "new"), 3, FakeSession(_routes())), TODAY, ema=False)


# --- build ------------------------------------------------------------------------------


def _cache_with(tmp_path: Path, posts_to_store: set[int], ema: bool = True) -> sh.Cache:
    cache = sh.Cache(tmp_path / "cache")
    when = sh._now()
    listing = (SAMPLES / "eof_listing_page1.html").read_text(encoding="utf-8")
    cache.store(sh.LISTING, listing.encode("utf-8"), {}, when)
    posts = {p.post_id: p for p in sh.parse_listing(listing)[0]}
    list_post = (SAMPLES / "eof_post_list_2026-10-31.html").read_bytes()
    notice = (SAMPLES / "eof_post_shortage_tegsedi.html").read_bytes()
    ban_post = (
        '<html><body><div class="entry-content"><p>πατήστε <a href="/wp-content/uploads/2026/08/'
        'BAN.pdf">εδώ</a></p></div></body></html>'
    ).encode()
    for pid in posts_to_store:
        p = posts[pid]
        body = {"limited_availability": list_post, "export_ban": ban_post}.get(p.kind, notice)
        cache.store(p.url, body, {}, when)
        for f in sh.parse_post(body.decode("utf-8"), p.url)[1]:
            cache.store(f, b"%PDF-1.4 x", {}, when)
    if ema:
        xlsx = _ema_xlsx(tmp_path / "ema.xlsx", EMA_ROWS)
        cache.store(sh.EMA_SHORTAGES_XLSX, xlsx.read_bytes(), {}, when)
    return cache


@pytest.fixture
def fake_pdfs(monkeypatch):
    def pdf_tables(path):
        if "BAN" in path.name:
            raise sh.ScannedDocument(f"{path.name}: 9 pages without text (scanned)")
        return _tables("limited_availability_2026-10-31_tables.json")

    monkeypatch.setattr(sh, "pdf_tables", pdf_tables)


def test_build(tmp_path, fake_pdfs):
    cache = _cache_with(tmp_path, {10547, 10531, 10524, 10411, 10287, 9961})
    packages = _write_packages(tmp_path / "packages.csv", PACKAGES)
    otc = _write_packages(tmp_path / "otc.csv", OTC)
    links = tmp_path / "ema_links.csv"
    links.write_text("eof_code,ema_name\n326380102,Tegsedi\n", encoding="utf-8")
    rows, report = sh.build(cache, packages, otc, links, TODAY)
    by_type = {}
    for r in rows:
        by_type.setdefault(r["list_type"], []).append(r)

    la = by_type["limited_availability"]
    assert len(la) == 7 and all(r["matched_by"] == "barcode" for r in la)
    cytotec = next(r for r in la if r["eof_code"] == "192850101")
    assert cytotec["in_catalogue"] == "packages" and cytotec["published"] == "2026-10-31"
    assert (cytotec["start"], cytotec["valid_until"], cytotec["status"]) == (
        "2026-10-22",
        "2026-11-30",
        "Αυξημένη ζήτηση",
    )
    assert "ΟΡΘΗ ΕΠΑΝΑΛΗΨΗ" in cytotec["source_title"]  # the correction, not the first post
    assert cytotec["source_file"].endswith(".pdf")
    assert {r["eof_code"] for r in la if not r["in_catalogue"]} >= {"974700101"}  # Ι.Φ.Ε.Τ. import
    assert {"list_type": "limited_availability", "eof_code": "974700101"}.items() <= next(
        x for x in report["not_in_catalogue"] if x["eof_code"] == "974700101"
    ).items()

    # discontinuation notices: TEGSEDI (title: brand + strength), EMEND 125 mg matched, 80 mg not
    disc = by_type["discontinuation"]
    assert {r["eof_code"] for r in disc if r["eof_code"]} == {"326380102", "264660301"}
    assert any(u["name"] == "EMEND 80MG" for u in report["unmatched"])

    assert "export_ban" not in by_type  # scanned: no rows, but listed as pending
    assert [p["kind"] for p in report["pending"]] == ["export_ban"]
    assert "scanned" in report["pending"][0]["reason"]

    eu = by_type["eu_shortage"]
    assert {r["eof_code"] for r in eu} == {"326380102", "308100101", "308100102", ""}
    assert all(r["name"] != "Dynastat" for r in eu)  # resolved: not current
    bronchitol = next(r for r in eu if r["eof_code"] == "308100101")
    assert (
        bronchitol["section"] == "Expected resolution: March 2027" and bronchitol["published"] == "2025-11-14"
    )
    assert report["current"]["eu_shortage"]["statuses"]["Shortage resolved"] == 1
    assert any(u["name"] == "Human normal immunoglobulins" for u in report["unmatched"])

    assert len(report["history"]) == 6 and report["history"][0]["kind"] == "discontinuation"
    assert all(s["sha256"] for s in report["sources"])
    assert report["current"]["limited_availability"]["stale"] is False


def test_build_falls_back_to_an_older_list_and_says_so(tmp_path, fake_pdfs):
    cache = _cache_with(tmp_path, {10287}, ema=False)  # only the first 31/10 post on disk
    rows, report = sh.build(cache, _write_packages(tmp_path / "p.csv", PACKAGES), None, None, TODAY)
    info = report["current"]["limited_availability"]
    assert info["stale"] is True and info["rows"] == 7
    pending = {(p["kind"], p["reason"]) for p in report["pending"]}
    assert ("limited_availability", "newest list not downloaded, using an older one") in pending
    assert ("eu_shortage", "not downloaded") in pending
    assert ("discontinuation", "not downloaded") in pending  # notices not on disk


def test_cli_build_writes_csv_and_report(tmp_path, fake_pdfs):
    cache = _cache_with(tmp_path, {10531})
    packages = _write_packages(tmp_path / "packages.csv", PACKAGES)
    out = tmp_path / "out"
    args = [
        "build",
        "--cache",
        str(cache.root),
        "--packages",
        str(packages),
        "--otc",
        str(tmp_path / "none.csv"),
    ]
    assert sh.main([*args, "--out", str(out), "--today", "2026-11-14"]) == 0
    with (out / "shortages.csv").open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        assert tuple(reader.fieldnames) == sh.FIELDS
        assert sum(1 for _ in reader) > 0
    report = json.loads((out / "shortages_report.json").read_text(encoding="utf-8"))
    assert report["inputs"]["packages"]["sha256"]
