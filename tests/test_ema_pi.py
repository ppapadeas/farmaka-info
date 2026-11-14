"""EMA product information in Greek -> structured JSON (ingest/ema_pi.py), offline.

samples/ema/pi_lines_sample.txt holds synthetic text lines in the format printed by
`python -m ingest.ema_pi lines` (position, size, bold/italic, text; table rows as cells).
The layout follows the Greek PI PDFs of the EMA: divider pages, a running header, page
numbers, QRD headings; product, company and body text are invented. 13 pages: Annex I
with one SmPC, Annex II, Annex III with labelling and two package leaflets.
"""

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest
import requests

from ingest import ema_pi
from ingest.ema import Client
from ingest.ema_pi import (
    Line,
    Page,
    ParseError,
    PiSource,
    download,
    join_lines,
    lines_from_text,
    lines_to_text,
    parse_all,
    parse_pages,
    read_sources,
    split_parts,
    strip_furniture,
)

SAMPLE = Path(__file__).resolve().parent.parent / "samples" / "ema" / "pi_lines_sample.txt"
HEADER = "Δοκιμίνη – Πληροφορίες προϊόντος έκδοση 7"


def sample_pages() -> list[Page]:
    return lines_from_text(SAMPLE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def parsed() -> dict:
    return parse_pages(sample_pages())


def section(doc: dict, number: str) -> dict:
    (s,) = [s for s in doc["sections"] if s["number"] == number]
    return s


def texts(blocks: list[dict]) -> list[str]:
    out = []
    for b in blocks:
        if b["type"] == "list":
            out.extend(b["items"])
        elif b["type"] == "table":
            out.extend(c for r in b["rows"] for c in r)
        else:
            out.append(b["text"])
    return out


def all_text(res: dict) -> str:
    """Every string of the output, in order, without whitespace."""
    parts = texts(res["front"])
    for d in res["smpc"] + res["pil"]:
        parts.append(d.get("title", ""))
        parts.extend(texts(d["preamble"]))
        for s in d["sections"]:
            parts.extend([s["number"], s["title"], *texts(s["blocks"])])
    return "".join("".join(parts).split())


# --- structure ---------------------------------------------------------------------


def test_parts_from_divider_pages(parsed):
    assert parsed["parts"] == {
        "smpc": [1, 4],
        "annex_ii": [5, 6],
        "annex_iii": [7, 7],
        "labelling": [8, 9],
        "pil": [10, 13],
    }
    assert parsed["front"] == []
    assert parsed["warnings"] == []


def test_smpc_sections_numbers_and_titles(parsed):
    (smpc,) = parsed["smpc"]  # the labelling's "1. ΟΝΟΜΑΣΙΑ ..." does not open a second one
    numbers = [s["number"] for s in smpc["sections"]]
    assert numbers == list(ema_pi.SMPC_EXPECTED)
    assert section(smpc, "1")["title"] == "ΟΝΟΜΑΣΙΑ ΤΟΥ ΦΑΡΜΑΚΕΥΤΙΚΟΥ ΠΡΟΪΟΝΤΟΣ"
    assert section(smpc, "4.2")["title"] == "Δοσολογία και τρόπος χορήγησης"
    # a title wrapped onto a second bold line
    assert section(smpc, "4.5")["title"] == (
        "Αλληλεπιδράσεις με άλλα φαρμακευτικά προϊόντα και άλλες μορφές αλληλεπίδρασης"
    )
    assert section(smpc, "4.8")["page"] == 3
    assert smpc["pages"] == [2, 4]
    assert smpc["products"] == [
        "Δοκιμίνη 10 mg επικαλυμμένα με λεπτό υμένιο δισκία",
        "Δοκιμίνη 20 mg επικαλυμμένα με λεπτό υμένιο δισκία",
    ]
    assert section(smpc, "4")["blocks"] == []  # only its subsections have text


def test_text_before_section_1_is_the_preamble(parsed):
    (smpc,) = parsed["smpc"]
    assert texts(smpc["preamble"]) == [
        "Το φάρμακο αυτό τελεί υπό συμπληρωματική παρακολούθηση. Αυτό θα επιτρέψει τον ταχύ "
        "προσδιορισμό νέων πληροφοριών ασφάλειας."
    ]


def test_running_header_and_page_numbers_removed():
    pages = sample_pages()
    stripped, removed = strip_furniture(pages)
    before = [ln.text for pg in pages for ln in pg.lines]
    after = [ln.text for pg in stripped for ln in pg.lines]
    assert before.count(HEADER) == 8 and HEADER not in after
    assert sum(removed.values()) == 8 + 13  # header on 8 pages, a number on all 13
    assert not any(re.fullmatch(r"\d+", t) for t in after)
    assert "3 χρόνια." in after  # body text that starts with a number stays


def test_paragraphs_rejoined(parsed):
    (smpc,) = parsed["smpc"]
    assert texts(section(smpc, "2")["blocks"])[0] == (
        "Κάθε δισκίο περιέχει 10 mg δοκιμίνης. Χορηγείται με προσοχή σε ασθενείς με "
        "ινσουλινο-εξαρτώμενο διαβήτη."
    )
    dosage = texts(section(smpc, "4.2")["blocks"])
    # one paragraph across the page break (2 -> 3), the running header in between removed
    assert dosage[1].endswith("από την κλινική εικόνα και συνεχίζεται όσο υπάρχει όφελος για τον ασθενή.")
    # numbered lines start paragraphs; "(3) μήνες" after a line break inside a sentence does not
    assert dosage[3:] == [
        "1) Δύο δόσεις των 10 mg με μεσοδιάστημα τριών ωρών.",
        "2) Εφάπαξ δόση των 20 mg.",
        "Η ηπατική λειτουργία ελέγχεται στις 2 εβδομάδες και ξανά στους τρεις (3) μήνες μετά την έναρξη.",
    ]


def test_line_breaks_of_the_author_are_kept(parsed):
    (smpc,) = parsed["smpc"]
    assert texts(section(smpc, "7")["blocks"]) == [
        "Δοκιμή Φάρμα Α.Ε.\nΟδός Παραδείγματος 1\n11111 Αθήνα\nΕλλάδα"
    ]
    assert texts(section(smpc, "8")["blocks"]) == ["EU/1/99/999/001\nEU/1/99/999/002"]


def test_lists_subheadings_and_tables(parsed):
    (smpc,) = parsed["smpc"]
    assert section(smpc, "4.1")["blocks"] == [
        {"type": "paragraph", "text": "Η Δοκιμίνη ενδείκνυται σε ενήλικες:"},
        {
            "type": "list",
            "level": 1,
            "items": [
                "για τη θεραπεία της δοκιμαστικής νόσου σε ασθενείς που δεν ανταποκρίνονται σε άλλες "
                "θεραπείες ή δεν τις ανέχονται",
                "για την πρόληψη υποτροπών",
            ],
        },
    ]
    assert section(smpc, "4.2")["blocks"][0] == {"type": "subheading", "text": "Δοσολογία"}
    assert section(smpc, "4.4")["blocks"][0] == {"type": "subheading", "text": "Ηπατοτοξικότητα"}  # italic
    blocks = section(smpc, "4.8")["blocks"]
    assert [b["type"] for b in blocks] == ["subheading", "table", "subheading", "paragraph"]
    assert blocks[1]["rows"] == [
        ["Κατηγορία οργανικού συστήματος", "Πολύ συχνές", "Συχνές"],
        ["Διαταραχές του νευρικού συστήματος", "Κεφαλαλγία", "Ζάλη"],
        ["Διαταραχές του γαστρεντερικού", "Ναυτία", ""],
    ]


def test_two_package_leaflets(parsed):
    assert len(parsed["pil"]) == 2
    for pil in parsed["pil"]:
        assert pil["title"] == "Φύλλο οδηγιών χρήσης: Πληροφορίες για τον ασθενή"
        assert [s["number"] for s in pil["sections"]] == ["1", "2", "3", "4", "5", "6"]
    first, second = parsed["pil"]
    assert first["products"] == ["Δοκιμίνη 10 mg επικαλυμμένα με λεπτό υμένιο δισκία"]
    assert second["products"] == ["Δοκιμίνη 20 mg επικαλυμμένα με λεπτό υμένιο δισκία"]
    assert [s["title"] for s in first["sections"]] == [
        "Τι είναι η Δοκιμίνη και ποια είναι η χρήση της",
        "Τι πρέπει να γνωρίζετε πριν πάρετε τη Δοκιμίνη",
        "Πώς να πάρετε τη Δοκιμίνη",
        "Πιθανές ανεπιθύμητες ενέργειες",
        "Πώς να φυλάσσετε τη Δοκιμίνη",
        "Περιεχόμενα της συσκευασίας και λοιπές πληροφορίες",
    ]
    assert [s["page"] for s in first["sections"]] == [11, 11, 11, 11, 12, 12]
    assert first["pages"] == [11, 12] and second["pages"] == [13, 13]


def test_leaflet_contents_list_is_not_taken_for_headings(parsed):
    first, second = parsed["pil"]
    for pil in (first, second):  # plain in the first leaflet, bold in the second
        toc = [t for t in texts(pil["preamble"]) if re.match(r"\d\. ", t)]
        assert len(toc) == 6
        assert section(pil, "1")["blocks"][0]["text"].startswith("Η Δοκιμίνη")


def test_instructions_after_section_6_stay_in_section_6(parsed):
    first = parsed["pil"][0]
    assert texts(section(first, "6")["blocks"]) == [
        "Τι περιέχει η Δοκιμίνη",
        "Η δραστική ουσία είναι η δοκιμίνη.",
        "Οδηγίες χρήσης",
        "1. Πλύνετε τα χέρια σας",
        "2. Ανοίξτε την κυψέλη",
    ]


# --- never drop text ---------------------------------------------------------------


def test_every_line_of_smpc_and_pil_pages_is_in_the_output(parsed):
    pages, _ = strip_furniture(sample_pages())
    _, part_lines, _ = split_parts(pages)
    out = all_text(parsed)
    lines = part_lines["smpc"] + part_lines["pil"]
    assert len(lines) > 100
    for ln in lines:
        text = " ¦ ".join(ln.cells) if ln.table else ln.text
        text = re.sub(r"^(?:\d{1,2}(?:\.\d{1,2})?\.?\s|[•\-]\s)", "", text).replace("¦", "")
        assert "".join(text.split()) in out, ln


def test_text_that_looks_like_a_heading_stays_in_its_section(parsed):
    (smpc,) = parsed["smpc"]
    # "5. Οι μελέτες ..." is numbered but neither bold nor the title of section 5
    assert texts(section(smpc, "4.6")["blocks"])[-1].startswith("5. Οι μελέτες σε ζώα")


def test_text_above_a_new_leaflet_goes_with_that_leaflet(parsed):
    first, second = parsed["pil"]
    assert texts(second["preamble"])[0] == "Σημείωση που προηγείται του τίτλου."
    assert "Σημείωση" not in " ".join(texts(section(first, "6")["blocks"]))


def without(text: str, *drop: str, page: int | None = None) -> list[Page]:
    """The sample without the lines whose text is one of `drop` (on `page` only, if given)."""
    keep, current = [], 0
    for ln in text.splitlines():
        if ln.startswith("# page "):
            current = int(ln.split()[2])
        elif (page is None or current == page) and any(ln.endswith("\t" + d) for d in drop):
            continue
        keep.append(ln)
    return lines_from_text("\n".join(keep))


def test_missing_required_section_fails_and_names_it():
    raw = SAMPLE.read_text(encoding="utf-8")
    with pytest.raises(ParseError, match=r"^SmPC 1 \(p\. 2\): sections not found: 4\.3$"):
        parse_pages(without(raw, "4.3 Αντενδείξεις"))
    # the heading line of section 4 of the second leaflet only (its contents list keeps "4. ...")
    pages = without(raw, "4. Πιθανές ανεπιθύμητες ενέργειες", page=13)
    pages[-1].lines.insert(8, next(ln for ln in sample_pages()[-1].lines if ln.text.startswith("4. Πιθανές")))
    with pytest.raises(ParseError, match=r"^PIL 2 \(p\. 13\): sections not found: 4$"):
        parse_pages(pages)


def test_missing_divider_fails():
    raw = SAMPLE.read_text(encoding="utf-8")
    with pytest.raises(ParseError, match="Annex III B divider"):
        parse_pages(without(raw, "Β. ΦΥΛΛΟ ΟΔΗΓΙΩΝ ΧΡΗΣΗΣ"))
    with pytest.raises(ParseError, match="no text"):
        parse_pages([Page(1, 595, 842, [])])


def replaced(old: str, new: str, count: int = -1) -> list[Page]:
    return lines_from_text(SAMPLE.read_text(encoding="utf-8").replace(old, new, count))


# Variants seen in real EMA files (14/11/2026)


def test_contents_list_with_other_numbers():
    # Hizentra, Remsima: the contents list of a later leaflet is numbered 7-12 / 8-14
    raw = SAMPLE.read_text(encoding="utf-8").split("# page 11 ")
    for n in range(1, 7):
        raw[1] = raw[1].replace(f"\t-\t{n}. ", f"\t-\t{n + 6}. ", 1)
    res = parse_pages(lines_from_text("# page 11 ".join(raw)))
    assert [len(p["sections"]) for p in res["pil"]] == [6, 6]
    assert texts(res["pil"][0]["preamble"])[-6] == "7. Τι είναι η Δοκιμίνη και ποια είναι η χρήση της"


@pytest.mark.parametrize(
    "title",
    ["Τι περιέχει αυτό το φυλλάδιο οδηγιών:", "Πληροφορίες για τον ασθενή"],  # NovoThirteen; no title
)
def test_contents_list_other_title_or_none(title):
    res = parse_pages(replaced("B\tΤι περιέχει το παρόν φύλλο οδηγιών", "B\t" + title, 1))
    first = res["pil"][0]
    assert [len(p["sections"]) for p in res["pil"]] == [6, 6]
    assert section(first, "1")["blocks"][0]["text"] == "Η Δοκιμίνη περιέχει τη δραστική ουσία δοκιμίνη."


def test_leaflet_title_without_chrisis():
    # Ebixa: "Φυλλο οδηγιων: Πληροφοριες για το χρηστη" (no accents, no "χρήσης")
    raw = SAMPLE.read_text(encoding="utf-8")
    old = "Φύλλο οδηγιών χρήσης: Πληροφορίες για τον ασθενή"
    i = raw.rindex(old)
    res = parse_pages(
        lines_from_text(raw[:i] + "Φυλλο οδηγιων: Πληροφοριες για το χρηστη" + raw[i + len(old) :])
    )
    assert [len(p["sections"]) for p in res["pil"]] == [6, 6]
    assert res["pil"][1]["title"] == "Φυλλο οδηγιων: Πληροφοριες για το χρηστη"


def test_sentence_about_the_leaflet_is_not_a_new_leaflet():
    # Apidra: a bold line in section 3 starts "φύλλο οδηγιών χρήσης. Πρέπει να ..."
    old = "-\tΠάντοτε να παίρνετε το φάρμακο αυτό αυστηρά σύμφωνα με τις οδηγίες του γιατρού σας."
    new = "B\tφύλλο οδηγιών χρήσης. Πρέπει να παίρνετε το φάρμακο αυτό όπως σας είπε ο γιατρός."
    res = parse_pages(replaced(old, new))
    assert len(res["pil"]) == 2
    assert texts(section(res["pil"][0], "3")["blocks"]) == [new.split("\t")[1]]


def test_misnumbered_smpc_heading_is_read_by_its_title():
    # Renvela: "6.2 Κατάλογος εκδόχων" for 6.1, then "6.2 Ασυμβατότητες"
    (smpc,) = parse_pages(replaced("B\t6.1 Κατάλογος εκδόχων", "B\t6.2 Κατάλογος εκδόχων"))["smpc"]
    assert [s["number"] for s in smpc["sections"]] == list(ema_pi.SMPC_EXPECTED)
    assert section(smpc, "6.1")["title"] == "Κατάλογος εκδόχων"


def test_misnumbered_leaflet_heading_is_read_by_its_title():
    # HBVaxPro: "2. Τι είναι το HBVAXPRO ..." for section 1
    raw = SAMPLE.read_text(encoding="utf-8")
    head = "B\t1. Τι είναι η Δοκιμίνη και ποια είναι η χρήση της"
    i = raw.rindex(head)
    res = parse_pages(lines_from_text(raw[:i] + head.replace("1.", "2.") + raw[i + len(head) :]))
    second = res["pil"][1]
    assert [s["number"] for s in second["sections"]] == ["1", "2", "3", "4", "5", "6"]
    assert res["warnings"] == [
        "PIL 2: heading '2. Τι είναι η Δοκιμίνη και ποια είναι η χρήση της' read as section 1"
    ]


def test_divider_variants():
    for divider in ("B. ΦΥΛ039BΟ ΟΔΗΓΙΩΝ ΧΡΗΣΗΣ", ". ΦΥΛΛΟ ΟΔΗΓΙΩΝ ΧΡΗΣΗΣ", "A. ΦΥΛΛΟ ΟΔΗΓΙΩΝ ΧΡΗΣΗΣ",
                    "Β. ΦΥΛΛΟ Ο∆ΗΓΙΩΝ ΧΡΗΣΗΣ", "Β. ΦΥΛΛΟ ΟΔΗΓΙΩΝ ΓΙΑ ΤΟΝ ΧΡΗΣΤΗ"):  # fmt: skip
        res = parse_pages(replaced("Β. ΦΥΛΛΟ ΟΔΗΓΙΩΝ ΧΡΗΣΗΣ", divider))
        assert res["parts"]["pil"] == [10, 13], divider
    assert ema_pi.clean("Ο∆ΗΓΙΩΝ") == "ΟΔΗΓΙΩΝ"


def test_typo_in_leaflet_heading():
    # INOmax: "5. Πώς να φυλλάσετε το INOmax"
    res = parse_pages(replaced("B\t5. Πώς να φυλάσσετε τη Δοκιμίνη", "B\t5. Πώς να φυλλάσετε τη Δοκιμίνη"))
    assert [s["title"] for s in res["pil"][0]["sections"]][4] == "Πώς να φυλλάσετε τη Δοκιμίνη"


def test_no_annex_i_divider_when_the_file_starts_with_section_1():
    # Libmyris: page 1 is already "1. ΟΝΟΜΑΣΙΑ ΤΟΥ ΦΑΡΜΑΚΕΥΤΙΚΟΥ ΠΡΟΪΟΝΤΟΣ"
    raw = SAMPLE.read_text(encoding="utf-8")
    pages = without(raw, "ΠΑΡΑΡΤΗΜΑ Ι", "ΠΕΡΙΛΗΨΗ ΤΩΝ ΧΑΡΑΚΤΗΡΙΣΤΙΚΩΝ ΤΟΥ ΠΡΟΪΟΝΤΟΣ", page=1)
    pages = [pg for pg in pages if pg.number != 1]
    text = [ln for ln in pages[0].lines if "συμπληρωματική" in ln.text or "ασφάλειας." in ln.text]
    pages[0].lines = [ln for ln in pages[0].lines if ln not in text]  # section 1 first
    res = parse_pages(pages)
    assert res["parts"]["smpc"] == [2, 4] and len(res["smpc"][0]["sections"]) == 28
    assert res["warnings"] == ["no Annex I divider page: the SmPC starts on page 1"]


def test_numbered_lines_in_the_text_are_not_main_sections():
    raw = SAMPLE.read_text(encoding="utf-8")
    # Imatinib Teva: "10 ημέρες/μήνα." at the start of a line in 5.1
    pages = lines_from_text(raw.replace("-\tΗ Δοκιμίνη απορροφάται ταχέως.", "-\t10 ημέρες/μήνα."))
    (smpc,) = parse_pages(pages)["smpc"]
    assert texts(section(smpc, "5.2")["blocks"]) == ["10 ημέρες/μήνα."]
    # Eydenzelt: bold numbered steps in 6.6 ("7. Ελέγξτε για φυσαλίδες αέρα.") are not 7, 8, ...
    step = "B\t7. Ελέγξτε για φυσαλίδες αέρα."
    pages = lines_from_text(raw.replace("-\tΚαμία ειδική υποχρέωση για απόρριψη.", step))
    (smpc,) = parse_pages(pages)["smpc"]
    assert [s["number"] for s in smpc["sections"]] == list(ema_pi.SMPC_EXPECTED)
    assert texts(section(smpc, "6.6")["blocks"]) == ["7. Ελέγξτε για φυσαλίδες αέρα."]
    assert section(smpc, "7")["title"] == "ΚΑΤΟΧΟΣ ΤΗΣ ΑΔΕΙΑΣ ΚΥΚΛΟΦΟΡΙΑΣ"


def test_framed_text_is_not_a_table():
    # Levetiracetam Hospira: whole pages in a ruled frame, bullets in a second "column"
    framed = [("4.2 Δοσολογία και τρόπος χορήγησης", ""), ("Δοσολογία", ""), ("", "• για τη θεραπεία"),
              ("Η θεραπεία μπορεί να ξεκινήσει", ""), ("από το στόμα.", "")]  # fmt: skip
    assert not ema_pi.is_table(framed)
    assert not ema_pi.is_table([("Μία στήλη", ""), ("δεύτερη γραμμή", "")])
    adverse = [("Λοιμώξεις και παρασιτώσεις", ""), ("Πολύ συχνές", "Ρινοφαρυγγίτιδα"), ("Συχνές", "Γρίπη"),
               ("Διαταραχές του αίματος", ""), ("Πολύ συχνές", "Θρομβοπενία")]  # fmt: skip
    assert ema_pi.is_table(adverse)
    # Vitrakvi: sections 6.5-10 laid out in a ruled grid
    grid = [("6.5 Φύση και συστατικά του περιέκτη", "", ""), ("Υάλινη φιάλη", "100 ml", "1 φιάλη"),
            ("7. ΚΑΤΟΧΟΣ ΤΗΣ ΑΔΕΙΑΣ ΚΥΚΛΟΦΟΡΙΑΣ", "", ""), ("Bayer AG", "Leverkusen", "Γερμανία")]  # fmt: skip
    assert not ema_pi.is_table(grid)


def with_divider(after: int, title: str) -> list[Page]:
    """The sample with a divider page inserted after page `after`, pages renumbered."""
    pages = sample_pages()
    pages.insert(after, Page(0, 595.32, 841.92, [L(0, 350.5, title, bold=True)]))
    return [
        Page(n, p.width, p.height, [replace(ln, page=n) for ln in p.lines]) for n, p in enumerate(pages, 1)
    ]


def test_the_same_divider_twice():
    # Nuwiq: a second "A. ΕΠΙΣΗΜΑΝΣΗ" divider among the labelling pages
    res = parse_pages(with_divider(9, "Α. ΕΠΙΣΗΜΑΝΣΗ"))
    assert res["parts"]["labelling"] == [8, 10] and res["parts"]["pil"] == [11, 14]
    # a part that opens again after another part is an error
    with pytest.raises(ParseError, match=r"part labelling opens twice \(pages 3 and 9\)"):
        parse_pages(with_divider(2, "Α. ΕΠΙΣΗΜΑΝΣΗ"))


def test_pdf_in_another_language_fails():
    # Ifirmacombi, Perjeta: the "Greek" URL of the EMA served a Latvian / Estonian file
    pages = [Page(1, 595, 842, [L(1, 350, "ZĀĻU APRAKSTS"), L(1, 380, "Zarnu angioedēma: ir ziņots")])]
    with pytest.raises(ParseError, match=r"not in Greek \(0% of the letters are Greek\)"):
        parse_pages(pages)


def test_characters_missing_from_the_text_layer_fail():
    # Norvir, Humira, Lumigan: pdfminer drops the characters of some fonts; pdfium reads them
    pages = sample_pages()
    for pg in pages:
        pg.chars_read = pg.chars_pdfium = 1000
    assert parse_pages(pages)["smpc"]
    pages[2].chars_read = 900
    with pytest.raises(ParseError, match="characters missing from the PDF text layer: 12900 read of 13000"):
        parse_pages(pages)


def L(page: int, top: float, text: str, bold: bool = False, x0: float = 70.9) -> Line:
    return Line(page, x0, x0 + 5.5 * len(text), top, top + 11, 11, text, bold=bold)


def smpc_lines(page: int, name: str) -> list[Line]:
    heads = ["1. ΟΝΟΜΑΣΙΑ ΤΟΥ ΦΑΡΜΑΚΕΥΤΙΚΟΥ ΠΡΟΪΟΝΤΟΣ", "2. ΠΟΙΟΤΙΚΗ ΚΑΙ ΠΟΣΟΤΙΚΗ ΣΥΝΘΕΣΗ"]
    heads += [f"4.{n} {ema_pi.SMPC_TITLES[(4, n)]}" for n in (1, 2, 3, 4, 5, 6, 8)]
    heads += ["7. ΚΑΤΟΧΟΣ ΤΗΣ ΑΔΕΙΑΣ ΚΥΚΛΟΦΟΡΙΑΣ", "10. ΗΜΕΡΟΜΗΝΙΑ ΑΝΑΘΕΩΡΗΣΗΣ ΤΟΥ ΚΕΙΜΕΝΟΥ"]
    out = []
    for k, h in enumerate(heads):
        out.append(L(page, 100 + 50 * k, h, bold=True))
        out.append(L(page, 125 + 50 * k, f"{name}: κείμενο {k}."))
    return out


PIL_HEADS = [
    "1. Τι είναι το Β και ποια είναι η χρήση του",
    "2. Τι πρέπει να γνωρίζετε πριν πάρετε το Β",
    "3. Πώς να πάρετε το Β",
    "4. Πιθανές ανεπιθύμητες ενέργειες",
    "5. Πώς να φυλάσσετε το Β",
    "6. Περιεχόμενα της συσκευασίας και λοιπές πληροφορίες",
]


def test_several_smpcs_in_annex_i():
    pil = [L(5, 60 + 50 * k, h, bold=True) for k, h in enumerate(PIL_HEADS)]
    pil += [L(5, 85 + 50 * k, f"Κείμενο {k + 1}.") for k in range(6)]
    pages = [
        Page(1, 595, 842, [L(1, 350, "ΠΑΡΑΡΤΗΜΑ Ι", bold=True)]),
        Page(2, 595, 842, smpc_lines(2, "Α")),
        Page(
            3,
            595,
            842,
            [L(3, 60, "▼Το φάρμακο αυτό τελεί υπό συμπληρωματική παρακολούθηση."), *smpc_lines(3, "Β")],
        ),
        Page(4, 595, 842, [L(4, 350, "Β. ΦΥΛΛΟ ΟΔΗΓΙΩΝ ΧΡΗΣΗΣ", bold=True)]),
        Page(5, 595, 842, sorted(pil, key=lambda ln: ln.top)),
    ]
    res = parse_pages(pages)
    assert len(res["smpc"]) == 2
    a, b = res["smpc"]
    assert texts(section(a, "10")["blocks"]) == ["Α: κείμενο 10."]
    assert texts(b["preamble"]) == ["▼Το φάρμακο αυτό τελεί υπό συμπληρωματική παρακολούθηση."]
    assert (a["pages"], b["pages"]) == ([2, 2], [3, 3])
    missing = "3, 4, 4.7, 4.9, 5, 5.1, 5.2, 5.3, 6, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 8, 9"
    assert res["warnings"] == [
        f"SmPC 1: sections not found: {missing}",
        f"SmPC 2: sections not found: {missing}",
    ]


def test_leaflet_with_only_empty_sections_fails():
    # a contents list taken for the headings leaves six empty sections: not written
    pages = [
        Page(1, 595, 842, [L(1, 350, "ΠΑΡΑΡΤΗΜΑ Ι", bold=True)]),
        Page(2, 595, 842, smpc_lines(2, "Α")),
        Page(3, 595, 842, [L(3, 350, "Β. ΦΥΛΛΟ ΟΔΗΓΙΩΝ ΧΡΗΣΗΣ", bold=True)]),
        Page(4, 595, 842, [L(4, 60 + 20 * k, h, bold=True) for k, h in enumerate(PIL_HEADS)]),
    ]
    with pytest.raises(ParseError, match=r"PIL 1 \(p\. 4\): all sections empty"):
        parse_pages(pages)


def test_smpc_heading_variants():
    lines = smpc_lines(2, "Α")
    # "1 ΟΝΟΜΑΣΙΑ ..." without the dot; "1 ml ..." in the text is not a heading
    lines[0] = replace(lines[0], text="1 ΟΝΟΜΑΣΙΑ ΤΟΥ ΦΑΡΜΑΚΕΥΤΙΚΟΥ ΠΡΟΪΟΝΤΟΣ")
    lines[3] = replace(lines[3], text="1 ml διαλύματος περιέχει 4 mg.")
    # a bold "5.4 RM" inside 4.6 is not a section; "Αντενδείξεις" without its number is 4.3
    lines.insert(13, L(2, 466, "5.4 RM", bold=True))
    k = next(n for n, ln in enumerate(lines) if ln.text.startswith("4.3 "))
    lines[k] = replace(lines[k], text="Αντενδείξεις")
    pages = [
        Page(1, 595, 842, [L(1, 350, "ΠΑΡΑΡΤΗΜΑ Ι", bold=True)]),
        Page(2, 595, 842, sorted(lines, key=lambda ln: ln.top)),
        Page(3, 595, 842, [L(3, 350, "Β. ΦΥΛΛΟ ΟΔΗΓΙΩΝ ΧΡΗΣΗΣ", bold=True)]),
        Page(4, 595, 842, [L(4, 60 + 50 * k, h, bold=True) for k, h in enumerate(PIL_HEADS)]
             + [L(4, 85 + 50 * k, "Κείμενο.") for k in range(6)]),
    ]  # fmt: skip
    pages[3].lines.sort(key=lambda ln: ln.top)
    (smpc,) = parse_pages(pages)["smpc"]
    assert [s["number"] for s in smpc["sections"]] == ["1", "2", "4.1", "4.2", "4.3", "4.4", "4.5", "4.6", "4.8",
                                                       "7", "10"]  # fmt: skip
    assert texts(section(smpc, "2")["blocks"]) == ["1 ml διαλύματος περιέχει 4 mg."]
    assert section(smpc, "4.3")["title"] == "Αντενδείξεις"


def test_note_above_the_annex_i_title_is_kept_as_front():
    pages = sample_pages()
    note = L(1, 50, "Το παρόν έγγραφο αποτελεί τις εγκεκριμένες πληροφορίες προϊόντος.")
    pages[0].lines.insert(0, note)
    res = parse_pages(pages)
    assert res["front"] == [{"type": "paragraph", "text": note.text}]
    assert res["parts"]["front"] == [1, 1] and res["parts"]["smpc"] == [1, 4]


# --- small pieces ------------------------------------------------------------------


def test_join_lines():
    assert join_lines("ινσουλινο-", "εξαρτώμενο") == "ινσουλινο-εξαρτώμενο"
    assert join_lines("αιμορ­", "ραγία") == "αιμορραγία"  # soft hyphen: a break point
    assert join_lines("25 mg/", "ml") == "25 mg/ml"
    assert join_lines("μεταξύ 15 -", "30 µg") == "μεταξύ 15 - 30 µg"
    assert join_lines("δισκίο", "ημερησίως") == "δισκίο ημερησίως"


def test_symbol_font_glyphs_are_read_as_greek():
    chars = [{"text": "", "fontname": "ABCDEF+SymbolMT"}, {"text": "", "fontname": "Symbol"}]
    assert ema_pi._unmap_private("90 g", chars) == "90 μg"
    assert ema_pi._unmap_private(" x", chars) == "• x"
    wing = [{"text": "", "fontname": "Wingdings-Regular"}]
    assert ema_pi._unmap_private(" x", wing) == "➢ x"
    assert ema_pi._unmap_private("", [{"text": "", "fontname": "Arial"}]) == ""


def test_fixture_format_round_trip():
    pages = sample_pages()
    assert lines_from_text(lines_to_text(pages)) == pages


# --- download, with a fake session -------------------------------------------------


def response(status: int, ctype: str = "", body: bytes = b"") -> requests.Response:
    r = requests.Response()
    r.status_code = status
    r.headers.update({"Content-Type": ctype, "Last-Modified": "Wed, 21 Oct 2026 14:36:09 GMT"})
    r._content = body
    r._content_consumed = True
    return r


class FakeSession:
    def __init__(self, answers: dict[str, list[requests.Response]]):
        self.answers = answers
        self.calls: list[str] = []

    def request(self, method: str, url: str, **kw) -> requests.Response:
        self.calls.append(url)
        return self.answers[url].pop(0)


def src(n: int) -> PiSource:
    return PiSource(
        f"EMEA/H/C/{n:06d}", f"Product {n}", f"https://www.ema.europa.eu/el/documents/p{n}_el.pdf"
    )


def test_download_skips_what_is_on_disk_and_resumes(tmp_path):
    a, b, c = src(1), src(2), src(3)
    s = FakeSession(
        {x.url: [response(200, "application/pdf", b"%PDF-1.7 " + x.name.encode())] for x in (a, b, c)}
    )
    client = Client(pause=0, session=s)
    assert download(client, [a, b, c], tmp_path, limit=2, log=lambda m: None) == (2, [])
    assert download(client, [a, b, c], tmp_path, log=lambda m: None) == (1, [])  # only the missing one
    assert download(client, [a, b, c], tmp_path, log=lambda m: None) == (0, [])
    assert s.calls == [a.url, b.url, c.url]
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert [e["product_number"] for e in manifest] == [
        "EMEA/H/C/000001",
        "EMEA/H/C/000002",
        "EMEA/H/C/000003",
    ]
    e = manifest[0]
    assert re.fullmatch(r"EMEA-H-C-000001_\d{8}T\d{6}Z\.pdf", e["file"])
    assert re.fullmatch(r"\d{8}T\d{6}Z", e["retrieved_at"]) and len(e["sha256"]) == 64
    assert (tmp_path / e["file"]).read_bytes() == b"%PDF-1.7 Product 1"
    assert e["last_modified"] == "Wed, 21 Oct 2026 14:36:09 GMT"
    assert not list(tmp_path.glob("*.part"))


def test_download_reports_failures_and_stops_when_refused(tmp_path):
    html = response(200, "text/html", b"<html>")
    bad = [src(n) for n in range(10, 10 + ema_pi.MAX_FAILURES_IN_A_ROW + 2)]
    answers = {bad[0].url: [html]} | {x.url: [response(404, "text/html")] for x in bad[1:]}
    client = Client(pause=0, session=FakeSession(answers))
    done, failures = download(client, bad, tmp_path, log=lambda m: None)
    assert done == 0 and len(failures) == ema_pi.MAX_FAILURES_IN_A_ROW  # then it stops asking
    assert failures[0][1].startswith("NotAPdf: not a PDF (text/html")
    assert failures[1][1].startswith("HTTPError: 404")
    assert not list(tmp_path.glob("*.pdf*"))


class CutResponse(requests.Response):
    def iter_content(self, chunk_size=1, decode_unicode=False):
        yield b"%PDF-1.7 half"
        raise requests.exceptions.ChunkedEncodingError("Response ended prematurely")


def test_download_dropped_connection_leaves_nothing_and_retries_later(tmp_path):
    cut = CutResponse()
    cut.status_code, cut._content_consumed = 200, True
    a = src(1)
    s = FakeSession({a.url: [cut, response(200, "application/pdf", b"%PDF-1.7 whole")]})
    client = Client(pause=0, session=s)
    done, failures = download(client, [a], tmp_path, log=lambda m: None)
    assert done == 0 and failures[0][1].startswith("ChunkedEncodingError")
    assert list(tmp_path.iterdir()) == []
    assert download(client, [a], tmp_path, log=lambda m: None) == (1, [])


def test_read_sources_one_per_product(tmp_path):
    links = tmp_path / "ema_links.csv"
    links.write_text(
        "eof_code,ema_name,ema_product_number,inn,pi_el_url,epar_url,matched_by\n"
        "1,GONAL-f,EMEA/H/C/000071,x,https://e/gonal_el.pdf,https://e/g,name+substance\n"
        "2,GONAL-f,EMEA/H/C/000071,x,https://e/gonal_el.pdf,https://e/g,name+substance\n"
        "3,NoPI,EMEA/H/C/000072,x,,https://e/n,name+substance\n",
        encoding="utf-8",
    )
    assert read_sources(links) == [PiSource("EMEA/H/C/000071", "GONAL-f", "https://e/gonal_el.pdf")]


# --- parse run ---------------------------------------------------------------------


def test_parse_all_writes_one_json_per_product_and_a_report(tmp_path, monkeypatch):
    cache, out = tmp_path / "pi", tmp_path / "current" / "ema_pi"
    cache.mkdir()
    manifest = []
    for n in (71, 72):
        name = f"EMEA-H-C-{n:06d}_20261114T120412Z.pdf"
        (cache / name).write_bytes(b"%PDF-")
        manifest.append({"product_number": f"EMEA/H/C/{n:06d}", "name": f"P{n}", "url": f"https://e/{n}_el.pdf",
                         "file": name, "sha256": "ab" * 32, "bytes": 5, "retrieved_at": "20261114T120412Z",
                         "last_modified": ""})  # fmt: skip
    (cache / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    out.mkdir(parents=True)
    (out / "EMEA-H-C-000099.json").write_text("{}", encoding="utf-8")  # from an earlier run
    good = SAMPLE.read_text(encoding="utf-8")
    monkeypatch.setattr(
        ema_pi,
        "read_pdf",
        lambda path: (
            lines_from_text(good) if "000071" in path.name else without(good, "Β. ΦΥΛΛΟ ΟΔΗΓΙΩΝ ΧΡΗΣΗΣ")
        ),
    )
    report = parse_all(cache, out, jobs=1, log=lambda m: None)
    assert sorted(p.name for p in out.iterdir()) == ["EMEA-H-C-000071.json"]
    doc = json.loads((out / "EMEA-H-C-000071.json").read_text(encoding="utf-8"))
    assert doc["schema_version"] == 1 and doc["language"] == "el"
    assert doc["product_number"] == "EMEA/H/C/000071" and doc["name"] == "P71"
    assert doc["source"]["publisher"] == "European Medicines Agency"
    assert doc["source"]["retrieved_at"] == "2026-11-14T12:04:12Z" and doc["source"]["pages"] == 13
    assert doc["source"]["attribution"].startswith("Πηγή: Ευρωπαϊκός Οργανισμός Φαρμάκων (EMA)")
    assert [len(d["sections"]) for d in doc["smpc"]] == [28] and len(doc["pil"]) == 2
    assert report["summary"]["parsed"] == 1 and report["summary"]["failed"] == 1
    (failed,) = report["failed"]
    assert failed["product_number"] == "EMEA/H/C/000072" and "Annex III B divider" in failed["reason"]
    on_disk = json.loads((tmp_path / "current" / "ema_pi_report.json").read_text(encoding="utf-8"))
    assert on_disk["summary"] == report["summary"]
