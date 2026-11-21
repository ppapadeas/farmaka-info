"""EOF SmPCs and leaflets in sections (ingest/eof_pi.py), on official files in samples/raw/:

- eof_PL_1899907_2.pdf   leaflet, current (QRD) template (ΔΕΞΤΡΟΖΗ/ΒΙΟΣΕΡ)
- eof_SPC_1898004_1.pdf  short SmPC: water for injection has no 4.5-4.9
- eof_PL_1898004_1.pdf   leaflet of 2011 in the older format, its own numbered headings
- eof_PL_1899601_2.doc   leaflet as a Word file (LibreOffice converts it)
"""

import json
import re
import shutil
from pathlib import Path

import pytest

from ingest import eof_pi
from sitegen import leaflets

RAW = Path(__file__).resolve().parent.parent / "samples" / "raw"


def _numbers(doc):
    return [s["number"] for s in doc["sections"]]


def test_eof_leaflet_in_the_template():
    res = eof_pi.parse_document(RAW / "eof_PL_1899907_2.pdf", "pil")
    assert res["layout"] == "qrd"
    (doc,) = res["docs"]
    assert _numbers(doc) == ["1", "2", "3", "4", "5", "6"]
    # page numbers are not left in the text as paragraphs
    for s in doc["sections"]:
        for b in s["blocks"]:
            assert not re.fullmatch(r"\d{1,3}", (b.get("text") or "").strip())
    g = leaflets.glance(doc)
    assert "χρησιμοποιείται" in g["use"]["text"] and g["before"]


def test_eof_short_smpc_is_still_the_template():
    res = eof_pi.parse_document(RAW / "eof_SPC_1898004_1.pdf", "smpc")
    assert res["layout"] == "qrd"
    found = set(_numbers(res["docs"][0]))
    assert {"4.1", "4.2", "6", "7"} <= found and "4.8" not in found


def test_eof_old_leaflet_is_split_at_its_own_headings():
    res = eof_pi.parse_document(RAW / "eof_PL_1898004_1.pdf", "pil")
    assert res["layout"] == "numbered"
    doc = res["docs"][0]
    assert doc["sections"][0]["title"] == "ΠΡΟΣΔΙΟΡΙΣΜΟΣ ΦΑΡΜΑΚΕΥΤΙΚΟΥ ΠΡΟΪΟΝΤΟΣ"
    assert _numbers(doc) == ["1", "2", "3", "4"]


@pytest.mark.skipif(eof_pi.soffice() is None, reason="LibreOffice is not installed")
def test_eof_word_leaflet(tmp_path):
    pdfs = eof_pi.convert([RAW / "eof_PL_1899601_2.doc"], tmp_path)
    res = eof_pi.parse_document(pdfs[RAW / "eof_PL_1899601_2.doc"], "pil")
    assert res["layout"] == "qrd"
    # a repeated title page is not kept as an empty leaflet
    assert len(res["docs"]) == 1 and _numbers(res["docs"][0]) == ["1", "2", "3", "4", "5", "6"]


def _root(tmp_path):
    root = tmp_path / "eof"
    (root / "pdf").mkdir(parents=True)
    docs, products = {}, {}
    for code, files, name in (
        ("1899907", [("pil", "eof_PL_1899907_2.pdf")], "ΔΕΞΤΡΟΖΗ/ΒΙΟΣΕΡ INJ.SO.INF 5.5 (5) % W/V"),
        (
            "1898004",
            [("pil", "eof_PL_1898004_1.pdf"), ("smpc", "eof_SPC_1898004_1.pdf")],
            "ΥΔΩΡ ΓΙΑ ΕΝΕΣΙΜΑ ΕΦ.(IV)/DEMO SOLV.INJ",
        ),
    ):
        listed = []
        for kind, f in files:
            shutil.copy(RAW / f, root / "pdf" / f)
            url = f"https://services.eof.gr/human-search/download?id={code}&type={kind}"
            docs[url] = {
                "path": f"pdf/{f}",
                "format": "pdf",
                "type": kind,
                "sha256": f"{code}-{kind}",
                "pdf_modified": "2024-01-08",
                "retrieved_at": "2026-11-14T12:34:31+00:00",
            }
            listed.append({"type": kind, "url": url})
        products[code] = {
            "state": "done",
            "name": name,
            "eof_codes": [f"{code}01"],
            "documents": listed,
        }
    (root / "manifest.json").write_text(
        json.dumps({"documents": docs, "products": products}, ensure_ascii=False), encoding="utf-8"
    )
    return root


def test_parse_all_then_the_package_page(tmp_path):
    root, out = _root(tmp_path), tmp_path / "eof_pi"
    logs = []
    rep = eof_pi.parse_all(root, out, jobs=1, log=logs.append)
    assert rep["products"] == 2 and rep["errors"] == 0
    assert rep["layouts"] == {"pil:numbered": 1, "pil:qrd": 1, "smpc:qrd": 1}
    d = json.loads((out / "1899907.json").read_text(encoding="utf-8"))
    assert d["product_number"] == "EOF-1899907" and d["source"]["short"] == "ΕΟΦ"
    assert d["source"]["date"] == "2024-01-08"
    # nothing changed: nothing parsed again
    logs.clear()
    eof_pi.parse_all(root, out, jobs=1, log=logs.append)
    assert "0 to parse" in logs[0]

    index = leaflets.load_eof(out)
    top, body = leaflets.package_parts(index["EOF-1899907"], "ΔΕΞΤΡΟΖΗ/ΒΙΟΣΕΡ INJ.SO.INF 5% BAG")
    assert "Αυτούσια από το φύλλο οδηγιών · ΕΟΦ · 08/01/2024" in top
    assert 'id="fyllo"' in body and "Εθνικός Οργανισμός Φαρμάκων (ΕΟΦ)" in body and "/fylla/" not in body
    # older format: the text in its sections, no «Με μια ματιά»; the SmPC on the same page
    top, body = leaflets.package_parts(index["EOF-1898004"], "ΥΔΩΡ ΓΙΑ ΕΝΕΣΙΜΑ ΕΦ.(IV)/DEMO SOLV.INJ 100ML")
    assert top == ""
    assert "ΠΡΟΣΔΙΟΡΙΣΜΟΣ ΦΑΡΜΑΚΕΥΤΙΚΟΥ ΠΡΟΪΟΝΤΟΣ" in body
    assert 'id="pxp"' in body and 'href="#fp-s4.1"' in body and 'href="#pxp"' in body
    ids = set(re.findall(r'id="([^"]+)"', body))
    assert set(re.findall(r'href="#([^"]+)"', body)) <= ids


def test_errors_are_listed_in_the_log(tmp_path):
    # a file that is not a PDF: reported with its product, type and link, the run goes on
    root, out = _root(tmp_path), tmp_path / "eof_pi"
    (root / "pdf" / "eof_PL_1700000_1.pdf").write_bytes(b"not a pdf")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    url = "https://services.eof.gr/human-search/download?id=1700000&type=pil"
    manifest["documents"][url] = {
        "path": "pdf/eof_PL_1700000_1.pdf",
        "format": "pdf",
        "type": "pil",
        "sha256": "x",
    }
    manifest["products"]["1700000"] = {
        "state": "done",
        "name": "X",
        "eof_codes": ["170000001"],
        "documents": [{"type": "pil", "url": url}],
    }
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    logs = []
    rep = eof_pi.parse_all(root, out, jobs=1, log=logs.append)
    assert rep["products"] == 2 and rep["errors"] == 1
    (line,) = [x for x in logs if "error:" in x]
    assert line.startswith("  error: 1700000 pil: ") and line.endswith(f"<{url}>")
    # the next run tries the failed product again (a parser fix reaches it without a new
    # parser version), and only that one
    logs.clear()
    rep = eof_pi.parse_all(root, out, jobs=1, log=logs.append)
    assert "1 to parse" in logs[0] and rep["errors"] == 1
