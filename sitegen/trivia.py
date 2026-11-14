"""Curiosities found in the data, recomputed on every build (the /perierga/ page).

Every fact is computed from the same records the site shows, links to the
packages it is about, and says which list it comes from. No fact is written by
hand: if the data changes, the page changes.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from ingest.normalize import _VARIANTS

from .drug import short_mah, split_name, unit_count
from .text import eur


def _brand(p: dict) -> str:
    return split_name(p["name"])[0]


def compute(pkgs: list[dict], history: list[dict], equiv: dict[str, list[str]], oldest: str) -> list[dict]:
    """Facts as dicts: {key, title, value, text, links: [(label, href)]}."""
    reg = [p for p in pkgs if p.get("kind") != "otc" and p["prices"]["retail"]]
    otc = [p for p in pkgs if p.get("kind") == "otc" and p["prices"]["retail"]]
    by_code = {p["eof_code"]: p for p in pkgs}
    facts: list[dict] = []

    def link(p: dict) -> tuple[str, str]:
        return (_brand(p), f"/farmako/{p['eof_code']}/")

    # 1-2. extremes
    top = max(reg, key=lambda p: p["prices"]["retail"])
    low = min(reg, key=lambda p: p["prices"]["retail"])
    facts.append(
        {
            "key": "max",
            "title": "Η ακριβότερη συσκευασία",
            "value": top["prices"]["retail"],
            "kind": "eur",
            "text": f"{top['name']}. Με τα ίδια χρήματα αγοράζει κανείς "
            f"{top['prices']['retail'] / low['prices']['retail']:,.0f} συσκευασίες της φθηνότερης.".replace(
                ",", "."
            ),
            "links": [link(top), link(low)],
        }
    )
    facts.append(
        {
            "key": "min",
            "title": "Η φθηνότερη συσκευασία",
            "value": low["prices"]["retail"],
            "kind": "eur",
            "text": low["name"],
            "links": [link(low)],
        }
    )

    # 3-5. price history since the oldest complete list
    series: dict[str, list[dict]] = defaultdict(list)
    for h in history:
        if h["field"] == "price_retail":
            series[h["eof_code"]].append(h)
    moves = []
    jumps = []  # one-step changes of 10x or more: more likely a list error than a price decision
    updates = Counter()
    for p in reg:
        ev = sorted(series.get(p["eof_code"], []), key=lambda h: h["published"])
        ups = [h for h in ev if h["change"] == "update" and h["old"] not in ("", None)]
        updates[p["eof_code"]] = len(ups)
        steps = [
            (float(h["old"]), float(h["new"]), h) for h in ups if float(h["old"]) > 0 and float(h["new"]) > 0
        ]
        big = [t for t in steps if max(t[0] / t[1], t[1] / t[0]) >= 10]
        if big:
            jumps.append((max(max(a / b, b / a) for a, b, _ in big), big[0], p))
            continue
        first = next(
            (h for h in ev if h["change"] in ("first_seen", "new") and h["new"] not in ("", None)), None
        )
        if not ups or first is None:
            continue
        start = float(first["new"])
        if start >= 1:
            moves.append(((p["prices"]["retail"] - start) / start, start, first["published"], p))
    if moves:
        drop = min(moves, key=lambda m: m[0])
        rise = max(moves, key=lambda m: m[0])
        facts.append(
            {
                "key": "drop",
                "title": "Η μεγαλύτερη πτώση τιμής",
                "value": drop[0] * 100,
                "kind": "pct",
                "text": f"{_brand(drop[3])}: από {eur(drop[1])} ({drop[2][:4]}) σε {eur(drop[3]['prices']['retail'])} σήμερα.",
                "links": [link(drop[3])],
            }
        )
        facts.append(
            {
                "key": "rise",
                "title": "Η μεγαλύτερη αύξηση",
                "value": rise[0] * 100,
                "kind": "pct",
                "text": f"{_brand(rise[3])}: από {eur(rise[1])} ({rise[2][:4]}) σε {eur(rise[3]['prices']['retail'])} σήμερα.",
                "links": [link(rise[3])],
            }
        )
    if jumps:
        j = max(jumps, key=lambda t: t[0])
        a, b, h = j[1]
        facts.append(
            {
                "key": "jumps",
                "title": "Άλματα που μοιάζουν με λάθος",
                "value": len(jumps),
                "kind": "int",
                "text": f"Συσκευασίες με αλλαγή τιμής ×10 ή περισσότερο σε ένα μόνο δελτίο. Η πιο ακραία: "
                f"{_brand(j[2])}, από {eur(a)} σε {eur(b)} ({h['published'][:4]}). Είτε λάθος σε κατάλογο είτε "
                "διόρθωση· δεν το ξέρουμε, γι' αυτό μένουν έξω από την πτώση και την αύξηση.",
                "links": [link(j[2])],
            }
        )
    busy_code, busy_n = updates.most_common(1)[0] if updates else (None, 0)
    if busy_n:
        busy = by_code[busy_code]
        facts.append(
            {
                "key": "busy",
                "title": "Η πιο ανήσυχη τιμή",
                "value": busy_n,
                "kind": "int",
                "text": f"Τόσες φορές άλλαξε η λιανική του {_brand(busy)} από το {oldest[:4]}.",
                "links": [link(busy)],
            }
        )
    stable = sum(
        1
        for p in reg
        if updates[p["eof_code"]] == 0
        and any(
            h["change"] == "first_seen" and h["published"] == oldest for h in series.get(p["eof_code"], [])
        )
    )
    facts.append(
        {
            "key": "stable",
            "title": "Ακίνητες από το " + oldest[:4],
            "value": stable,
            "kind": "int",
            "text": "Συσκευασίες που υπάρχουν από τον πρώτο κατάλογο που έχουμε και η λιανική τους δεν άλλαξε ούτε λεπτό.",
            "links": [],
        }
    )

    gone = {h["eof_code"] for h in history if h["change"] == "withdrawn"}
    back = {h["eof_code"] for h in history if h["change"] == "reappeared"}
    if gone:
        facts.append(
            {
                "key": "ghosts",
                "title": "Φαντάσματα",
                "value": len(back),
                "kind": "int",
                "text": f"Από τις {len(gone):,} ".replace(",", ".")
                + "συσκευασίες που χάθηκαν από κάποιον πλήρη κατάλογο, τόσες ξαναεμφανίστηκαν σε επόμενο.",
                "links": [],
            }
        )
    zeros = {h["eof_code"] for h in history if h["field"] == "price_retail" and h["new"] in ("0", "0.0")}
    if zeros:
        facts.append(
            {
                "key": "zero",
                "title": "Δωρεάν, κατά λάθος",
                "value": len(zeros),
                "kind": "int",
                "text": "Συσκευασίες που εμφανίστηκαν σε επίσημο κατάλογο με λιανική 0,00 €. Το site τις αγνοεί στο γράφημα.",
                "links": [],
            }
        )

    # 6-7. crowds and spreads among equivalents (same substance, strength, form)
    groups = {tuple(sorted(v)) for v in equiv.values()}
    crowd = max(groups, key=lambda g: len({_brand(by_code[c]) for c in g}), default=None)
    if crowd:
        ps = [by_code[c] for c in crowd]
        p0 = min(ps, key=lambda p: p["prices"]["retail"])
        facts.append(
            {
                "key": "crowd",
                "title": "Το πιο πολυσύχναστο ράφι",
                "value": len({_brand(p) for p in ps}),
                "kind": "int",
                "text": f"Διαφορετικές μάρκες για το ίδιο πράγμα: {', '.join(sorted(p0['substances']))}, "
                "ίδια περιεκτικότητα και μορφή.",
                "links": [link(p0)],
            }
        )
    best = None
    for g in groups:
        # binding prices only: the OTC list has indicative prices (e.g. 0,14 € for 100 tablets)
        ps = [by_code[c] for c in g if unit_count(by_code[c]["name"]) and by_code[c].get("kind") != "otc"]
        if len(ps) < 2:
            continue
        per = sorted(((p["prices"]["retail"] / unit_count(p["name"]), p) for p in ps), key=lambda t: t[0])
        lo, hi = per[0], per[-1]
        if lo[0] > 0 and (best is None or hi[0] / lo[0] > best[0]):
            best = (hi[0] / lo[0], lo, hi)
    if best:
        facts.append(
            {
                "key": "spread",
                "title": "Ίδια ουσία, άλλη τιμή",
                "value": best[0],
                "kind": "times",
                "text": f"Τόσες φορές ακριβότερο ανά τεμάχιο το {_brand(best[2][1])} ({eur(best[2][0])}) από το "
                f"{_brand(best[1][1])} ({eur(best[1][0])}): ίδια ουσία και περιεκτικότητα, και τα δύο από το στόμα, "
                "άμεσης αποδέσμευσης.",
                "links": [link(best[1][1]), link(best[2][1])],
            }
        )

    # 8-9. reimbursement
    reimb = [
        (p["prices"]["retail"] - p["reimbursement"][0]["reimbursement_price"], p)
        for p in reg
        if p["reimbursement"] and p["reimbursement"][0]["reimbursement_price"]
    ]
    if reimb:
        gap = max(reimb, key=lambda t: t[0])
        facts.append(
            {
                "key": "gap",
                "title": "Η μεγαλύτερη διαφορά για τον ασθενή",
                "value": gap[0],
                "kind": "eur",
                "text": f"Λιανική μείον τιμή αποζημίωσης στο {_brand(gap[1])}, πριν καν μπει η συμμετοχή.",
                "links": [link(gap[1])],
            }
        )
    daily = [
        (p["reimbursement"][0]["daily_cost"], p)
        for p in reg
        if p["reimbursement"] and p["reimbursement"][0]["daily_cost"]
    ]
    if daily:
        d = max(daily, key=lambda t: t[0])
        facts.append(
            {
                "key": "daily",
                "title": "Το ακριβότερο κόστος ημέρας",
                "value": d[0],
                "kind": "eur",
                "text": f"Κόστος ημερήσιας θεραπείας για το {_brand(d[1])}, όπως το γράφει ο κατάλογος αποζημίωσης.",
                "links": [link(d[1])],
            }
        )

    # 10-11. who and what
    mahs = Counter(short_mah(p["mah"]) for p in reg if p["mah"])
    if mahs:
        m, n = mahs.most_common(1)[0]
        facts.append(
            {
                "key": "mah",
                "title": "Ο πιο παραγωγικός",
                "value": n,
                "kind": "int",
                "text": f"Συσκευασίες στο δελτίο τιμών με κάτοχο άδειας {m}.",
                "links": [],
            }
        )
    longest = max(pkgs, key=lambda p: len(p["name"]))
    facts.append(
        {
            "key": "long",
            "title": "Η μακροσκελέστερη ονομασία",
            "value": len(longest["name"]),
            "kind": "chars",
            "text": f"Χαρακτήρες στο όνομα του {_brand(longest)}, όπως το γράφει το δελτίο.",
            "links": [link(longest)],
        }
    )

    # 12. non-prescription
    if otc:
        o = max(otc, key=lambda p: p["prices"]["retail"])
        facts.append(
            {
                "key": "otc",
                "title": "Το ακριβότερο χωρίς συνταγή",
                "value": o["prices"]["retail"],
                "kind": "eur",
                "text": f"{o['name']}, ενδεικτική τιμή λιανικής.",
                "links": [link(o)],
            }
        )

    # 13. the spreadsheets themselves
    facts.append(
        {
            "key": "headers",
            "title": "Ορθογραφία κατά βούληση",
            "value": len(_VARIANTS["mah"]),
            "kind": "int",
            "text": "Διαφορετικοί τρόποι που έχει γραφτεί η στήλη «Κάτοχος Άδειας Κυκλοφορίας» στα xlsx του "
            "Υπουργείου, ανάμεσά τους και το «ΟΝΟΝΑΣΙΑ ΚΑΚ».",
            "links": [],
        }
    )
    return facts
