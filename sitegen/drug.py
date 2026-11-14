"""Display helpers that read structure out of the official package names.

The price bulletin gives one free-text field per package, e.g.
`LIPITOR F.C.TAB 20MG/TAB ΒTx 14 (BLIST 2x 7)`. For display and for comparing
equivalent packages we pull out the brand, the dose form, the strength and,
for tablets and capsules, the number of units. Every helper returns None when
unsure; callers then fall back to the raw name (never guess a comparison).
"""

from __future__ import annotations

import re

_FORM_WORDS = {
    "TABLET",
    "TABLETS",
    "TABS",
    "TAB",
    "CAPS",
    "CAP",
    "SYR",
    "CREAM",
    "GEL",
    "OINT",
    "LOTION",
    "POWDER",
    "DROPS",
    "SUPP",
    "PATCH",
    "SHAMPOO",
    "SPRAY",
    "GRANULES",
    "EMULSION",
    "SUSPENSION",
    "SOLUTION",
}
_DOTTED_FORM = re.compile(r"^(?=.*[A-Z])[A-Z/]+(?:\.[A-Z/]+)+\.?$")


def _is_form(tok: str) -> bool:
    t = tok.upper().strip(",")
    return t in _FORM_WORDS or bool(_DOTTED_FORM.match(t))


def split_name(name: str) -> tuple[str, str]:
    """'LIPITOR F.C.TAB 20MG/TAB ΒTx 14' -> ('LIPITOR', 'F.C.TAB 20MG/TAB ΒTx 14')."""
    toks = name.split()
    for i, t in enumerate(toks[1:], 1):
        if _is_form(t):
            return " ".join(toks[:i]).replace("®", "").replace("™", "").strip(), " ".join(toks[i:])
    return toks[0].replace("®", "") if toks else name, " ".join(toks[1:])


def dose_form(name: str) -> str | None:
    rest = split_name(name)[1]
    tok = rest.split()[0] if rest else ""
    return tok.upper() if tok and _is_form(tok) else None


def form_group(name: str) -> str | None:
    """Coarse dose-form family used to decide whether two packages compare."""
    f = dose_form(name)
    if not f:
        return None
    if "SYR" in f and "PF.SYR" not in f and "INJ" not in f:
        return "oral-liquid"
    if any(k in f for k in ("INJ", "INF", "VIAL", "IV", "IN.", "S.IN", "C/S", "PD.C", "SOL.IN")):
        return "parenteral"
    if "TAB" in f or "CAP" in f:
        return "oral-solid"
    if any(k in f for k in ("ORAL", "OR.", "SUS", "SYR", "DRO")):
        return "oral-liquid"
    if any(k in f for k in ("CREAM", "OINT", "GEL", "LOT", "PATCH", "CUT", "SHAMP")):
        return "topical"
    if any(k in f for k in ("INH", "NAS", "NEB")):
        return "inhalation"
    return f


_STRENGTH = re.compile(
    r"(\(\s*\d[\d.,]*(?:\s*\+\s*\d[\d.,]*)+\s*\)|\d[\d.,]*)\s*(MCG|MG|G|IU|I\.U\.|ML|%|MMOL|U)\b"
    r"(?:\s*/\s*(\d[\d.,]*\s*ML|ML|G|TAB|CAP|VIAL|DOSE|PF\.SYR|AMP|SACHET|H|24H))?",
    re.IGNORECASE,
)


def strength(name: str) -> tuple[str, str] | None:
    """(key, label): '20MG/TAB' -> ('20MG', '20 mg'); '(10+5)MG/TAB' -> ('10+5MG', '10+5 mg');
    '20MG/0,2 ML' -> ('20MG/0.2ML', '20 mg/0,2 ml'). Keys compare packages, labels are for people."""
    m = _STRENGTH.search(split_name(name)[1])
    if not m:
        return None
    amount = re.sub(r"[()\s]", "", m.group(1)).replace(",", ".")
    unit = m.group(2).upper().replace("I.U.", "IU")
    per = (m.group(3) or "").upper().replace(" ", "").replace(",", ".")
    if per in ("TAB", "CAP", "DOSE", "SACHET"):
        per = ""
    key = f"{amount}{unit}" + (f"/{per}" if per else "")
    unit_l = {"MCG": "mcg", "MG": "mg", "G": "g", "IU": "IU", "ML": "ml", "%": "%", "MMOL": "mmol", "U": "U"}[
        unit
    ]
    label = (amount.replace(".", ",") + ("" if unit == "%" else " ") + unit_l) + (
        f"/{per.lower().replace('.', ',')}" if per else ""
    )
    return key, label


_COUNT = re.compile(r"[BΒ][TΤ]\s*[xXΧ×]\s*(\d{1,4})(?!\s*[,.]\d)")


def unit_count(name: str) -> int | None:
    """Tablets/capsules in the pack ('BTx30', 'ΒTx 14', 'BT X 100 X 1'); None for other forms."""
    if form_group(name) != "oral-solid":
        return None
    m = _COUNT.search(name)
    n = int(m.group(1)) if m else 0
    return n if n > 1 else None


_LEGAL = {
    "LTD",
    "LIMITED",
    "GMBH",
    "AG",
    "SA",
    "S.A.",
    "A.E.",
    "Α.Ε.",
    "ΑΕ",
    "AE",
    "ΑΒΕΕ",
    "Α.Β.Ε.Ε.",
    "ΑΕΒΕ",
    "Α.Ε.Β.Ε.",
    "Μ.Ε.Π.Ε.",
    "ΜΕΠΕ",
    "Ε.Π.Ε.",
    "ΕΠΕ",
    "B.V.",
    "BV",
    "S.L.U.",
    "S.L.",
    "SL",
    "S.R.L.",
    "SRL",
    "S.P.A.",
    "SPA",
    "INC",
    "INC.",
    "A/S",
    "AB",
    "OY",
    "KFT",
    "D.O.O.",
    "N.V.",
    "NV",
    "PLC",
    "KG",
    "CO",
    "&",
    "LLC",
    "S.A.S.",
    "SAS",
    "Ι.Κ.Ε.",
    "ΙΚΕ",
    "Ο.Ε.",
    "ΟΕ",
    "Α.Β.Ε.Ε",
    "ΑΒΕΕ.",
    "LTD.",
    "GMBH,",
    "ABEE",
    "A.B.E.E.",
    "AEBE",
    "A.E",
    "MEPE",
    "M.E.P.E.",
    "EPE",
    "E.P.E.",
    "IKE",
}
_CUT = {
    "ΑΝΩΝΥΜΗ",
    "ΕΤΑΙΡΕΙΑ",
    "ΕΤΑΙΡΙΑ",
    "ΦΑΡΜΑΚΕΥΤΙΚΗ",
    "ΦΑΡΜΑΚΕΥΤΙΚΑ",
    "ΒΙΟΜΗΧΑΝΙΑ",
    "ΕΜΠΟΡΙΑ",
    "ΕΜΠΟΡΙΚΗ",
    "ΜΟΝΟΠΡΟΣΩΠΗ",
    "ΦΑΡΜΑΚΑΠΟΘΗΚΗ",
    "ΕΡΓΑΣΤΗΡΙΑ",
}


def short_mah(mah: str) -> str:
    """'VIATRIS HELLAS ΕΤΑΙΡΕΙΑ … Δ.Τ. VIATRIS HELLAS LTD, ΕΛΛΑΔΑ' -> 'Viatris Hellas'."""
    s = mah.split("Δ.Τ.")[-1] if "Δ.Τ." in mah else mah
    s = s.split(",")[0]
    words = s.split()
    out = []
    for w in words:
        if out and w.upper() in _CUT:
            break
        if w.upper() in _LEGAL:
            continue
        out.append(w)
    out = out[:3] or words[:1]
    return " ".join(
        "-".join(x.capitalize() for x in w.split("-")) if re.fullmatch(r"[A-Za-z][A-Za-z\-']*", w) else w
        for w in out
    )


def inn_label(s: str) -> str:
    """'METFORMIN HYDROCHLORIDE' -> 'Metformin hydrochloride'; Greek or odd names stay as they are."""
    return s[:1] + s[1:].lower() if re.fullmatch(r"[A-Z0-9 ,\-()/.']+", s) else s


def dispensing_label(s: str | None) -> str | None:
    """Dispensing category from the reimbursement list. The PDF truncates this
    column ('ΠΕΡΙΟΡΙΣΜΕΝΗ ΙΑΤΡ.ΣΥΝΤ. Η'), so only clear categories get a label."""
    if not s:
        return None
    u = s.upper()
    if "ΝΟΣΟΚΟΜΕΙΑΚΗ" in u and "ΜΟΝΟ" in u:
        return "Μόνο για νοσοκομειακή χρήση"
    if "ΠΙΝΑΚΑΣ" in u or "3459" in u:
        return "Ελεγχόμενη ουσία (ν. 3459/2006)"
    if "ΠΕΡΙΟΡΙΣΜΕΝΗ" in u:
        return "Με περιορισμένη ιατρική συνταγή"
    if "ΕΙΔΙΚΗ" in u:
        return "Με ειδική ιατρική συνταγή"
    if "ΣΥΝΤΑΓΗ" in u or "ΙΑΤΡΙΚΗ" in u:
        return "Με ιατρική συνταγή"
    return None


# Dose-form families for filters, and a plain Greek label per package. Built
# from the 191 form codes in the price list (F.C.TAB, SOL.INF, PD.C.S.INF …).
FORM_FAMILIES = {
    "tablet": "Δισκία",
    "capsule": "Καψάκια",
    "granules": "Κοκκία / φακελάκια",
    "oral_liquid": "Πόσιμα",
    "injection": "Ενέσιμα",
    "infusion": "Ορός / έγχυση",
    "topical": "Δερματικά",
    "eye": "Οφθαλμικά",
    "ear": "Ωτικά",
    "nasal": "Ρινικά",
    "inhalation": "Εισπνεόμενα",
    "vaginal": "Κολπικά",
    "rectal": "Ορθικά / υπόθετα",
    "patch": "Επιθέματα",
    "gas": "Ιατρικά αέρια",
    "other": "Άλλες μορφές",
}


def form_kind(name: str) -> tuple[str, str]:
    """(family, Greek label) for a package, e.g. ('tablet', 'Δισκία παρατεταμένης αποδέσμευσης')."""
    f = dose_form(name) or ""
    has = lambda *ks: any(k in f for k in ks)  # noqa: E731
    if not f:
        return "other", "Άλλη μορφή"
    if has("VAG"):
        return "vaginal", "Κολπικό"
    if has("SUPP"):
        return "rectal", "Υπόθετα"
    if has("REC"):
        return "rectal", "Ορθικό"
    if f.startswith(("EY", "EYE")) or has("OCU"):
        if has("OINT"):
            return "eye", "Οφθαλμική αλοιφή"
        if has("GEL"):
            return "eye", "Οφθαλμική γέλη"
        return "eye", "Οφθαλμικές σταγόνες"
    if f.startswith(("EAR", "EA.")):
        return "ear", "Ωτικές σταγόνες"
    if has("NAS", "N.SP"):
        return "nasal", "Ρινικό εκνέφωμα" if has("SP") else "Ρινικό"
    if has("INH", "AER"):
        return "inhalation", "Εισπνεόμενο"
    if has("MED.GAS"):
        return "gas", "Ιατρικό αέριο"
    if has("PATCH", "T.T.S", "TTS", "PLAST"):
        return "patch", "Διαδερμικό επίθεμα"
    if has("INF") or f.endswith(("IF", ".J.F", "INJF")) or f in ("I.V.",):
        return "infusion", "Ορός / έγχυση"
    if has(
        "INJ", "IN.", "C/S", "PFS", "PF.", "IMPL", "SOLV", "VIAL", "PD.S.IN", "PD.C.SO.IN", "S.I.", "B.V."
    ):
        if has("PF.P"):
            return "injection", "Ενέσιμο, προγεμισμένη πένα"
        if has("PFS", "PF.SYR", "PRFS"):
            return "injection", "Ενέσιμο, προγεμισμένη σύριγγα"
        return "injection", "Ενέσιμο"
    if has("GRAN", "GRA.", "GR.OR"):
        return "granules", "Αναβράζοντα κοκκία" if has("EFF") else "Κοκκία (φακελάκια)"
    if has("TAB") or f.endswith(".TA") or f.endswith("TA"):
        if has("EF", "SOLU"):
            return "tablet", "Αναβράζοντα δισκία"
        if has("CHW"):
            return "tablet", "Μασώμενα δισκία"
        if has("DISP", "ORO"):
            return "tablet", "Διασπειρόμενα δισκία"
        if has("PR.", "CON.R", "SR.", "MOD.R", "S.R", "CO.R"):
            return "tablet", "Δισκία παρατεταμένης αποδέσμευσης"
        if has("SUBL", "LING"):
            return "tablet", "Υπογλώσσια δισκία"
        if f.startswith("GR."):
            return "tablet", "Γαστροανθεκτικά δισκία"
        return "tablet", "Δισκία"
    if has("CAP", "CA.") or f.endswith(".CA"):
        if has("SOFT"):
            return "capsule", "Μαλακά καψάκια"
        if has("PR.", "MOD.R"):
            return "capsule", "Καψάκια παρατεταμένης αποδέσμευσης"
        if f.startswith(("GR.", "E.C.")):
            return "capsule", "Γαστροανθεκτικά καψάκια"
        return "capsule", "Καψάκια"
    if f == "SYR":
        return "oral_liquid", "Σιρόπι"
    if has("ORA", "OR.", "OR/", "LYOP", "DENTAL") or f.startswith("PS.OR"):
        return "oral_liquid", "Πόσιμο εναιώρημα" if has("SUS") else "Πόσιμο διάλυμα"
    if has("CREAM"):
        return "topical", "Κρέμα"
    if has("OINT"):
        return "topical", "Αλοιφή"
    if has("GEL"):
        return "topical", "Γέλη"
    if has("SHAMP"):
        return "topical", "Σαμπουάν"
    if has("CUT", "EXT", "SOAP", "LOT", "SPR."):
        return "topical", "Δερματικό σκεύασμα"
    return "other", "Άλλη μορφή"
