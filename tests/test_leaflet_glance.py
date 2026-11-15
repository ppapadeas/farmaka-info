"""«Με μια ματιά» and the leaflet inside the package page (sitegen/leaflets.py).

The documents below copy the structure of real EMA leaflets (Abraxane, Ajovy, Xagrid,
Exelon, CoAprovel, NovoRapid) as `python -m ingest.ema_pi parse` writes them.
"""

import re

from sitegen.leaflets import glance, package_parts, pick_doc


def P(text):
    return {"type": "paragraph", "text": text}


def H(text):
    return {"type": "subheading", "text": text}


def L(*items):
    return {"type": "list", "level": 1, "items": list(items)}


def sec(num, title, *blocks):
    return {"number": num, "title": title, "page": 1, "blocks": list(blocks)}


ABRAXANE = {
    "products": ["Abraxane 5 mg/ml κόνις για παρασκευή διασποράς προς έγχυση"],
    "sections": [
        sec(
            "1",
            "Τι είναι το Abraxane και ποια είναι η χρήση του",
            H("Τι είναι το Abraxane"),
            P(
                "Το Abraxane περιέχει, ως δραστική ουσία, την πακλιταξέλη. Η πακλιταξέλη ανήκει σε μια ομάδα "
                "φαρμάκων που ονομάζονται «ταξάνες» που χρησιμοποιούνται στον καρκίνο."
            ),
            H("Ποια είναι η χρήση του Abraxane"),
            P("Το Abraxane χρησιμοποιείται για τη θεραπεία των παρακάτω τύπων καρκίνου:"),
            P("Καρκίνος του μαστού"),
            L("Καρκίνος του μαστού που έχει εξαπλωθεί σε άλλα μέρη του σώματος."),
            P("Καρκίνος του παγκρέατος"),
            L("Το Abraxane χρησιμοποιείται μαζί με ένα φάρμακο που ονομάζεται γεμσιταβίνη."),
        ),
        sec(
            "2",
            "Τι πρέπει να γνωρίζετε πριν σας χορηγηθεί το Abraxane",
            H("Μη χρησιμοποιήσετε το Abraxane"),
            L("σε περίπτωση αλλεργίας"),
            H("Προειδοποιήσεις και προφυλάξεις:"),
            P("Απευθυνθείτε στον γιατρό σας."),
            H("Κύηση, θηλασμός και γονιμότητα"),
            P("Μη θηλάζετε."),
        ),
        sec(
            "3",
            "Πώς να χρησιμοποιήσετε το Abraxane",
            P(
                "Το Abraxane θα χορηγηθεί σε σας από το γιατρό σας εντός της φλέβας μέσω ενδοφλέβιας ενστάλαξης. "
                "Η συνήθης δόση για τον καρκίνο του μαστού είναι 260 mg/m2."
            ),
        ),
        sec(
            "4",
            "Πιθανές ανεπιθύμητες ενέργειες",
            P("Όπως όλα τα φάρμακα, έτσι και αυτό το φάρμακο μπορεί να προκαλέσει ανεπιθύμητες ενέργειες."),
            P(
                "Οι ακόλουθες πολύ συχνές ανεπιθύμητες ενέργειες μπορεί να επηρεάσουν περισσότερα από 1 στα 10 άτομα:"
            ),
            L(
                "Απώλεια μαλλιών (η πλειοψηφία των περιστατικών συνέβη σε λιγότερο από ένα μήνα)",
                "Εξάνθημα",
                "Ναυτία, διάρροια, δυσκοιλιότητα",
                "Πονοκέφαλος",
                "Ρίγη",
                "Βήχας",
                "Ζάλη",
                "Πυρετός",
            ),
            P("Οι συχνές ανεπιθύμητες ενέργειες μπορεί να επηρεάσουν έως και 1 στα 10 άτομα:"),
            L("Κνησμός"),
        ),
        sec(
            "5",
            "Πώς να φυλάσσετε το Abraxane",
            P(
                "Το φάρμακο αυτό πρέπει να φυλάσσεται σε μέρη που δεν το βλέπουν και δεν το φθάνουν τα παιδιά."
            ),
            P("Να μη χρησιμοποιείτε αυτό το φάρμακο μετά την ημερομηνία λήξης που αναφέρεται στο κουτί."),
            P(
                "Μη ανοιγμένα φιαλίδια: Φυλάσσετε το φιαλίδιο στο εξωτερικό κουτί για να προστατεύεται από το φως."
            ),
            P("Η διασπορά μπορεί να αποθηκευτεί σε ψυγείο (2°C-8°C) για έως και 24 ώρες."),
        ),
        sec(
            "6",
            "Περιεχόμενα της συσκευασίας και λοιπές πληροφορίες",
            P("Η δραστική ουσία είναι η πακλιταξέλη."),
        ),
    ],
}


def test_glance_answers_are_word_for_word():
    g = glance(ABRAXANE)
    # the medicine as subject, not the sentence about its class; the short lines that follow are the items
    assert g["use"]["text"] == "Το Abraxane χρησιμοποιείται για τη θεραπεία των παρακάτω τύπων καρκίνου:"
    assert g["use"]["items"] == ["Καρκίνος του μαστού", "Καρκίνος του παγκρέατος"]
    # how: the first sentence; the dose sentence is never shown out of its context
    assert g["how"]["text"].startswith("Το Abraxane θα χορηγηθεί σε σας") and "260" not in g["how"]["text"]
    assert g["before"] == [
        ("Μη χρησιμοποιήσετε το Abraxane", "fo-s2-h0"),
        ("Προειδοποιήσεις και προφυλάξεις", "fo-s2-h1"),
        ("Κύηση, θηλασμός και γονιμότητα", "fo-s2-h2"),
    ]
    vc = g["very_common"]
    assert vc["items"][:3] == ["Απώλεια μαλλιών", "Εξάνθημα", "Ναυτία, διάρροια, δυσκοιλιότητα"]
    assert len(vc["items"]) == 6 and vc["more"] == 2 and "Κνησμός" not in vc["items"]
    # storage: past the boilerplate; a reconstituted dispersion kept cold is no fridge label
    assert g["storage"]["text"].startswith("Μη ανοιγμένα φιαλίδια: Φυλάσσετε")
    assert g["storage"]["labels"] == []


def test_glance_dose_only_how_keeps_the_sub_headings():
    doc = {
        "sections": [
            sec(
                "3",
                "Πώς να πάρετε το X",
                P("Πάντοτε να παίρνετε το φάρμακο αυτό αυστηρά σύμφωνα με τις οδηγίες του γιατρού σας."),
                P("Η συνιστώμενη δόση είναι 20 mg μία φορά την ημέρα."),
                H("Εάν πάρετε μεγαλύτερη δόση X από την κανονική"),
                P("Επικοινωνήστε με τον γιατρό σας."),
                H("Εάν ξεχάσετε να πάρετε το X"),
                P("Μην πάρετε διπλή δόση."),
            )
        ]
    }
    how = glance(doc)["how"]
    assert how["text"] == ""
    assert how["subs"] == [
        ("Εάν πάρετε μεγαλύτερη δόση X από την κανονική", "fo-s3-h0"),
        ("Εάν ξεχάσετε να πάρετε το X", "fo-s3-h1"),
    ]


def test_glance_very_common_in_other_layouts():
    # Ajovy: heading and item in one block, separated by a newline
    ajovy = {
        "sections": [
            sec(
                "4",
                "",
                P(
                    "Πολύ συχνές (μπορεί να επηρεάσουν περισσότερα από 1 στα 10 άτομα)\n"
                    "Πόνος, σκλήρυνση ή ερυθρότητα στο σημείο της ένεσης"
                ),
                P("Συχνές (μπορεί να επηρεάσουν έως 1 στα 10 άτομα)\nΚνησμός στο σημείο της ένεσης"),
            )
        ]
    }
    assert glance(ajovy)["very_common"]["items"] == ["Πόνος, σκλήρυνση ή ερυθρότητα στο σημείο της ένεσης"]
    # Xagrid: a heading, then one paragraph of comma-separated items
    xagrid = {
        "sections": [
            sec(
                "4",
                "",
                H("Πολύ συχνές ανεπιθύμητες ενέργειες: μπορεί να επηρεάσουν περισσότερα από 1 στα 10 άτομα"),
                P("Πονοκέφαλος, ζάλη (αίσθημα αστάθειας, ίλιγγος), ναυτία."),
                H("Συχνές ανεπιθύμητες ενέργειες: μπορεί να επηρεάσουν έως και 1 στα 10 άτομα"),
                P("Διάρροια."),
            )
        ]
    }
    assert glance(xagrid)["very_common"]["items"] == ["Πονοκέφαλος", "ζάλη", "ναυτία"]
    # no very common category at all: no answer rather than the wrong one
    efient = {"sections": [sec("4", "", P("Συχνές ανεπιθύμητες ενέργειες:"), L("Ρινική αιμορραγία"))]}
    assert "very_common" not in glance(efient)


def test_glance_fridge_label_only_for_a_storage_instruction():
    def store(text):
        return glance({"sections": [sec("5", "", P(text))]})["storage"]["labels"]

    assert store("Φυλάσσετε και μεταφέρετε σε ψυγείο (2°C - 8°C).") == ["Ψυγείο 2–8 °C"]
    assert store("Φυλάσσετε σε ψυγείο (2 °C – 8 °C). Μην καταψύχετε.") == ["Ψυγείο 2–8 °C"]
    assert store("Μη φυλάσσετε σε ψυγείο (2°C - 8°C) ή σε καταψύκτη.") == []


def test_pick_doc_by_strength_form_and_sub_brand():
    exelon = [
        ["Exelon 1,5 mg σκληρά καψάκια", "Exelon 3 mg σκληρά καψάκια", "Exelon 6 mg σκληρά καψάκια"],
        ["Exelon 2 mg/ml πόσιμο διάλυμα"],
        ["Exelon 4,6 mg/24h διαδερμικό έμπλαστρο", "Exelon 9,5 mg/24h διαδερμικό έμπλαστρο"],
    ]
    assert pick_doc(exelon, "EXELON CAPS 3MG/CAP BTx28(BLIST2x14)") == 0
    assert pick_doc(exelon, "EXELON ORAL.SOL 2MG/ML FLx120ML") == 1
    assert pick_doc(exelon, "EXELON TTS 9,5MG/24h 30 Φακελλίσκοι") == 2
    coaprovel = [["CoAprovel 150 mg/12,5 mg δισκία"], ["CoAprovel 300 mg/12,5 mg δισκία"]]
    assert pick_doc(coaprovel, "COAPROVEL F.C.TAB (300+12,5)mg/TAB BT x 28") == 1
    novorapid = [
        ["NovoRapid 100 μονάδες/ml ενέσιμο διάλυμα σε φιαλίδιο"],
        ["NovoRapid Penfill 100 μονάδες/ml ενέσιμο διάλυμα σε φυσίγγιο"],
        ["NovoRapid FlexPen 100 μονάδες/ml ενέσιμο διάλυμα σε προγεμισμένη συσκευή τύπου πένας"],
    ]
    assert pick_doc(novorapid, "NOVORAPID INJ.SOL 100U/ML 1VIALX10ML") == 0
    assert pick_doc(novorapid, "NOVORAPID PENFILL INJ.SOL 100U/ML 5CARTRIDGESx3ML") == 1
    # nothing to go on: the first leaflet
    assert pick_doc([["A 10 mg δισκία"], ["A 20 mg δισκία"]], "A ORAL.SUSP") == 0


def test_package_parts_links_land_on_the_page():
    lf = {
        "slug": "abraxane",
        "texts": {"pil": [ABRAXANE], "smpc": True, "source": {"url": "https://example.org/a.pdf"}},
        "smpc_quick": [("Ενδείξεις", "d0-s4.1")],
    }
    top, body = package_parts(lf, "ABRAXANE PD.DIS.INF 5MG/ML VIALx 100MG")
    ids = set(re.findall(r'id="([^"]+)"', body))
    targets = set(re.findall(r'href="#([^"]+)"', top + body))
    assert targets and targets <= ids, targets - ids
    assert '<details class="lf-sec" id="fo-s1" open>' in body
    assert 'href="/fylla/abraxane/pxp/#d0-s4.1"' in body
    assert "Κείμενο για: Abraxane 5 mg/ml" in body
    assert package_parts({"slug": "x", "texts": {"pil": []}}, "X") == ("", "")
