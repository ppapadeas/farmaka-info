"""Greek names for active substances, from Wikidata (CC0).

The price list gives substances as English INNs in capitals ("METFORMIN
HYDROCHLORIDE"). Wikidata has Greek labels for many medicines, keyed by ATC
code (property P267). A substance gets a Greek name only when an ATC code of
its single-substance packages points to a Wikidata item whose English label is
the substance or its base (the INN before a salt word), and that gives exactly
one Greek label. Substances that never appear alone match only on an exact
English label. Anything else stays without a Greek name; the site then shows
the INN.

Usage:
    python -m ingest.substances_el --wikidata wd_atc_el.csv --packages data/current/packages.csv \
        --out reference/substances_el.csv
    python -m ingest.substances_el --fetch ...   # query Wikidata first (one request)
"""

from __future__ import annotations

import argparse
import csv
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import requests

SPARQL = "https://query.wikidata.org/sparql"
QUERY = (
    'SELECT ?item ?atc ?en ?el WHERE { ?item wdt:P267 ?atc . ?item rdfs:label ?el FILTER(lang(?el)="el") '
    'OPTIONAL { ?item rdfs:label ?en FILTER(lang(?en)="en") } }'
)
USER_AGENT = "farmaka-info-build/0.1 (https://github.com/ppapadeas/farmaka-info)"


def _norm(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def fetch(path: Path) -> None:
    r = requests.get(
        SPARQL,
        params={"query": QUERY},
        headers={"Accept": "text/csv", "User-Agent": USER_AGENT},
        timeout=120,
    )
    r.raise_for_status()
    path.write_bytes(r.content)


def load_wikidata(path: Path) -> dict[str, list[dict]]:
    by_atc: dict[str, list[dict]] = defaultdict(list)
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if not row.get("el") or not row.get("atc"):
                continue
            by_atc[row["atc"].strip()].append(
                {"qid": row["item"].rsplit("/", 1)[-1], "en": row.get("en") or "", "el": row["el"].strip()}
            )
    return by_atc


SALT_WORDS = {
    "sodium",
    "potassium",
    "calcium",
    "magnesium",
    "hydrochloride",
    "dihydrochloride",
    "hydrobromide",
    "sulfate",
    "sulphate",
    "maleate",
    "mesylate",
    "mesilate",
    "besylate",
    "besilate",
    "fumarate",
    "tartrate",
    "bitartrate",
    "citrate",
    "acetate",
    "succinate",
    "phosphate",
    "nitrate",
    "bromide",
    "chloride",
    "monohydrate",
    "dihydrate",
    "trihydrate",
    "hemihydrate",
    "anhydrous",
    "hyclate",
    "lactate",
    "gluconate",
    "propionate",
    "dipropionate",
    "valerate",
    "furoate",
    "xinafoate",
    "tosylate",
    "embonate",
    "pamoate",
    "disodium",
    "trihydrochloride",
    "hemifumarate",
    "medoxomil",
    "axetil",
    "pivoxil",
    "base",
    "and",
}


def matches(inn: str, en: str) -> bool:
    """English label is the INN, or they differ only by salt/hydrate words:
    'metformin' ~ 'METFORMIN HYDROCHLORIDE', 'levothyroxine sodium' ~ 'LEVOTHYROXINE'.
    'iron sucrose' does not match 'IRON' (sucrose is not a salt word)."""
    a, b = _norm(inn), _norm(en)
    if not (a and b):
        return False
    if a == b:
        return True
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    if not long_.startswith(short + " "):
        return False
    return all(w in SALT_WORDS for w in long_[len(short) + 1 :].split())


def greek_names(packages: Path, by_atc: dict[str, list[dict]]) -> list[dict]:
    alone: dict[str, Counter] = defaultdict(Counter)
    every: set[str] = set()
    with packages.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            subs = [s for s in r["substances"].split("|") if s]
            every.update(subs)
            if len(subs) == 1 and len(r["atc"]) == 7:
                alone[subs[0]][r["atc"]] += 1
    by_en: dict[str, set] = defaultdict(set)
    for items in by_atc.values():
        for it in items:
            by_en[_norm(it["en"])].add((it["el"], it["qid"]))
    out = []
    for inn in sorted(every):
        found: dict[str, tuple] = {}
        for atc, _ in alone.get(inn, Counter()).most_common():
            for it in by_atc.get(atc, []):
                if matches(inn, it["en"]):
                    found[it["el"].lower()] = (it["el"], it["qid"], atc, "atc+name")
        if not found:
            for el, qid in by_en.get(_norm(inn), ()):
                found[el.lower()] = (el, qid, "", "name")
        if len(found) == 1:
            el, qid, atc, how = next(iter(found.values()))
            out.append(
                {
                    "inn": inn,
                    "name_el": el[:1].upper() + el[1:],
                    "wikidata": qid,
                    "atc": atc,
                    "matched_by": how,
                }
            )
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wikidata", type=Path, required=True, help="CSV export (item, atc, en, el)")
    ap.add_argument("--fetch", action="store_true", help="query Wikidata into --wikidata first")
    ap.add_argument("--packages", type=Path, default=Path("data/current/packages.csv"))
    ap.add_argument("--out", type=Path, default=Path("reference/substances_el.csv"))
    a = ap.parse_args()
    if a.fetch:
        fetch(a.wikidata)
    rows = greek_names(a.packages, load_wikidata(a.wikidata))
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with a.out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["inn", "name_el", "wikidata", "atc", "matched_by"])
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} substances with a Greek name -> {a.out}")


if __name__ == "__main__":
    main()
