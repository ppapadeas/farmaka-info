import csv

from ingest.substances_el import greek_names, matches


def test_matches_base_and_salt():
    assert matches("ATORVASTATIN", "atorvastatin")
    assert matches("METFORMIN HYDROCHLORIDE", "metformin")
    assert matches("LEVOTHYROXINE", "levothyroxine sodium")
    assert not matches("METHOTREXATE", "metformin")
    assert not matches("IRON", "iron sucrose complex x")  # different substance, shares a word only at start
    assert not matches("", "x")


def _write(path, rows, fields):
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def test_greek_names_need_atc_and_name(tmp_path):
    pk = tmp_path / "packages.csv"
    _write(
        pk,
        [
            {"eof_code": "1", "atc": "C10AA05", "substances": "ATORVASTATIN"},
            {"eof_code": "2", "atc": "A10BA02", "substances": "METFORMIN HYDROCHLORIDE"},
            {"eof_code": "3", "atc": "N02BE01", "substances": "CAFFEINE"},  # ATC of another item
            {"eof_code": "4", "atc": "X00XX00", "substances": "PARACETAMOL|CAFFEINE"},
            {"eof_code": "5", "atc": "C10AA", "substances": "ATORVASTATIN CALCIUM TRIHYDRATE"},
        ],
        ["eof_code", "atc", "substances"],
    )
    by_atc = {
        "C10AA05": [{"qid": "Q1", "en": "atorvastatin", "el": "ατορβαστατίνη"}],
        "A10BA02": [{"qid": "Q2", "en": "metformin", "el": "μετφορμίνη"}],
        "N02BE01": [{"qid": "Q3", "en": "paracetamol", "el": "παρακεταμόλη"}],
    }
    got = {r["inn"]: (r["name_el"], r["matched_by"]) for r in greek_names(pk, by_atc)}
    assert got["ATORVASTATIN"] == ("Ατορβαστατίνη", "atc+name")
    assert got["METFORMIN HYDROCHLORIDE"] == ("Μετφορμίνη", "atc+name")
    assert got["PARACETAMOL"] == ("Παρακεταμόλη", "name")  # only in a combination: exact English label
    assert "CAFFEINE" not in got  # an ATC code alone is not enough
    assert got["ATORVASTATIN CALCIUM TRIHYDRATE"] == ("Ατορβαστατίνη", "salt of ATORVASTATIN")


def test_pil_greek_name():
    from ingest.substances_el import pil_greek_name

    assert (
        pil_greek_name("Τι περιέχει το Zyprexa - Η δραστική ουσία είναι η ολανζαπίνη. Κάθε δισκίο")
        == "ολανζαπίνη"
    )
    assert pil_greek_name("Η δραστική ουσία είναι η Ιντερφερόνη βήτα-1a 30 μικρογραμμάρια/0,5 ml") is None
    assert pil_greek_name("Η δραστική ουσία είναι η nonacog alfa (ανασυνδυασμένος παράγοντας)") is None
    assert pil_greek_name("Η δραστική ουσία είναι η λαμιβουδίνη 150 mg") == "λαμιβουδίνη"
