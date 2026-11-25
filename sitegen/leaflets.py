"""Reading pages for the official product information (EMA), from the JSON
written by `python -m ingest.ema_pi parse` (data/current/ema_pi/*.json).

Two pages per product: /fylla/<slug>/ (package leaflet, for patients) and
/fylla/<slug>/pxp/ (SmPC, for professionals). The text is shown as extracted,
never edited; the page names the EMA as the source and links the original pdf,
which prevails if anything differs.
"""

from __future__ import annotations

import json
import re
from email.utils import parsedate_to_datetime
from pathlib import Path

from . import pages
from .drug import strength
from .pages import e
from .text import date_gr, slugify


_FRIDGE = re.compile(
    r"[Φφ]υλάσσετε\s+(?:το\s+\w+\s+)?σε\s+ψυγείο\s*\(\s*2\s*°?\s*C?\s*(?:[–-]|έως)\s*8\s*[°o]?\s*C\s*\)"
)
_FREEZER = re.compile(r"[Φφ]υλάσσετε\s+(?:το\s+\w+\s+)?(?:σε\s+)?καταψύκτη")
_MONITOR = re.compile(r"συμπληρωματική\s+παρακολούθηση")

# Quick buttons: (label, section number) for the leaflet and the SmPC, plus
# leaflet sub-headings found by their opening words.
PIL_QUICK = [
    ("Πριν το πάρετε", "2"),
    ("Πώς να το πάρετε", "3"),
    ("Ανεπιθύμητες ενέργειες", "4"),
    ("Φύλαξη", "5"),
]
PIL_SUBS = [("Κύηση / θηλασμός", ("Κύηση", "Θηλασμός")), ("Οδήγηση", ("Οδήγηση",)), ("Παιδιά", ("Παιδιά",))]
SMPC_QUICK = [
    ("Ενδείξεις", "4.1"),
    ("Δοσολογία", "4.2"),
    ("Αντενδείξεις", "4.3"),
    ("Προειδοποιήσεις", "4.4"),
    ("Αλληλεπιδράσεις", "4.5"),
    ("Κύηση / γαλουχία", "4.6"),
    ("Οδήγηση", "4.7"),
    ("Ανεπιθύμητες", "4.8"),
    ("Φύλαξη", "6.4"),
]


def _text(section: dict) -> str:
    return " ".join(b.get("text") or " ".join(b.get("items", [])) for b in section.get("blocks", []))


def flags(d: dict) -> list[str]:
    """Labels read from the storage sections (SmPC 6.4, else leaflet 5) and the SmPC opening.

    One product can hold several forms with different storage (GONAL-f powder up
    to 25 °C, its pens in the fridge): a label that holds for only some of them
    says so."""
    docs = d.get("smpc") or d.get("pil") or []
    num = "6.4" if d.get("smpc") else "5"
    storages = [
        " ".join(_text(s) for s in doc.get("sections", []) if str(s.get("number")) == num) for doc in docs
    ]
    storages = [t for t in storages if t.strip()]
    out = []
    for rx, label in ((_FRIDGE, "Ψυγείο 2–8 °C"), (_FREEZER, "Κατάψυξη")):
        hits = sum(1 for t in storages if rx.search(t))
        if hits:
            out.append(label if hits == len(storages) else f"{label} (ορισμένες μορφές)")
    head = " ".join(
        b.get("text", "") for doc in d.get("smpc", [])[:1] for b in (doc.get("preamble") or [])
    ) + " ".join(_text(s) for doc in d.get("smpc", [])[:1] for s in doc.get("sections", [])[:1])
    if _MONITOR.search(head) or _MONITOR.search(json.dumps(d.get("smpc", [])[:1], ensure_ascii=False)[:3000]):
        out.append("▼ Συμπληρωματική παρακολούθηση")
    return out


def quick_links(d: dict, kind: str) -> list[tuple[str, str]]:
    """(label, fragment) for the first document of the leaflet or SmPC, only where it exists."""
    docs = d.get("pil" if kind == "pil" else "smpc") or []
    if not docs:
        return []
    secs = {str(s.get("number")): s for s in docs[0].get("sections", [])}
    out = [
        (label, f"d0-s{num}") for label, num in (PIL_QUICK if kind == "pil" else SMPC_QUICK) if num in secs
    ]
    if kind == "pil" and "2" in secs:
        heads = [b for b in secs["2"].get("blocks", []) if b.get("type") == "subheading"]
        for label, starts in PIL_SUBS:
            for k, b in enumerate(heads):
                if b.get("text", "").strip().startswith(starts):
                    out.insert(len(out) - 2, (label, f"d0-s2-h{k}"))
                    break
    return out


def _quick(links: list[tuple[str, str]], base: str = "") -> str:
    if not links:
        return ""
    return (
        '<nav class="quick" aria-label="Γρήγορη μετάβαση">'
        + "".join(f'<a class="chip" href="{e(base)}#{e(f)}">{e(label)}</a>' for label, f in links)
        + "</nav>"
    )


def _blocks(blocks: list[dict], prefix: str | None = None) -> str:
    out = []
    k = 0
    for b in blocks:
        t = b.get("type")
        if t == "paragraph":
            out.append(f"<p>{e(b['text'])}</p>")
        elif t == "subheading":
            anchor = f' id="{e(prefix)}-h{k}"' if prefix else ""
            k += 1
            out.append(f"<h4{anchor}>{e(b['text'])}</h4>")
        elif t == "list":
            lvl = int(b.get("level") or 1)
            items = "".join(f"<li>{e(x)}</li>" for x in b.get("items", []))
            out.append(f'<ul class="l{min(lvl, 3)}">{items}</ul>')
        elif t == "table":
            rows = "".join(
                "<tr>" + "".join(f"<td>{e(c)}</td>" for c in r) + "</tr>" for r in b.get("rows", [])
            )
            out.append(f'<div class="tw"><table class="pi-table">{rows}</table></div>')
    return "".join(out)


def _doc(doc: dict, i: int, multi: bool) -> str:
    head = ""
    if multi:
        head = f'<h2 class="pi-products" id="d{i}">{e(" · ".join(doc.get("products") or []) or doc.get("title", ""))}</h2>'
    pre = _blocks(doc.get("preamble") or [])
    parts = []
    for s in doc.get("sections", []):
        sid = f"d{i}-s{s['number']}"
        parts.append(
            f'<section class="pi-sec" id="{e(sid)}"><h3><span>{e(s["number"])}.</span> {e(s["title"])}</h3>'
            f"{_blocks(s['blocks'], sid)}</section>"
        )
    secs = "".join(parts)
    return f'<article class="pi-doc">{head}{f"<div class=pi-pre>{pre}</div>" if pre else ""}{secs}</article>'


def _toc(docs: list[dict], kind: str) -> str:
    if not docs:
        return ""
    first = docs[0]
    items = []
    for s in first.get("sections", []):
        num = str(s["number"])
        if kind == "smpc" and "." in num and not num.startswith("4."):
            continue  # keep the TOC short: top sections and 4.x (clinical particulars)
        items.append(f'<li><a href="#d0-s{e(num)}"><span>{e(num)}</span> {e(s["title"])}</a></li>')
    other = ""
    if len(docs) > 1:
        other = (
            '<p class="toc-h">Περιεκτικότητες / μορφές</p><ul>'
            + "".join(
                f'<li><a href="#d{i}">{e((d.get("products") or ["—"])[0])}</a></li>'
                for i, d in enumerate(docs)
            )
            + "</ul>"
        )
    return f'<nav class="pi-toc" aria-label="Περιεχόμενα"><p class="toc-h">Περιεχόμενα</p><ul>{"".join(items)}</ul>{other}</nav>'


def _source(d: dict) -> str:
    src = d.get("source") or {}
    when = ""
    if src.get("last_modified"):
        try:
            when = " · έκδοση EMA " + date_gr(parsedate_to_datetime(src["last_modified"]).date().isoformat())
        except (TypeError, ValueError):
            when = ""
    return (
        f'<p class="pi-src">{e(src.get("attribution") or "Πηγή: European Medicines Agency (EMA).")}{e(when)} · '
        f'<a href="{e(src.get("url", ""))}" rel="noopener">pdf ↗</a>. Το κείμενο εξάγεται αυτόματα από το pdf· '
        "αν κάτι διαφέρει, ισχύει το pdf.</p>"
    )


def _page(ctx, d: dict, slug: str, kind: str) -> str:
    docs = d["pil"] if kind == "pil" else d["smpc"]
    name = d.get("name") or d.get("product_number")
    tabs = []
    if d["pil"]:
        cur = ' aria-current="page"' if kind == "pil" else ""
        tabs.append(f'<a href="/fylla/{e(slug)}/"{cur}>Για τον ασθενή</a>')
    if d["smpc"]:
        cur = ' aria-current="page"' if kind == "smpc" else ""
        tabs.append(f'<a href="/fylla/{e(slug)}/pxp/"{cur}>Για επαγγελματίες (ΠΧΠ)</a>')
    title = "Φύλλο οδηγιών χρήσης" if kind == "pil" else "Περίληψη χαρακτηριστικών του προϊόντος"
    body = f"""
<div class="wrap">
<nav class="crumbs" aria-label="Διαδρομή"><a href="/">Αρχική</a> / <a href="/fylla/">Επίσημα κείμενα</a> / {e(name)}</nav>
<div class="page-head">
  <h1>{e(name)}</h1>
  <p>{title} · {e(d.get("product_number", ""))}</p>
  {'<div class="chips flags">' + "".join(f'<span class="chip flag">{e(f)}</span>' for f in flags(d)) + "</div>" if flags(d) else ""}
</div>
<div class="pi-tabs">{"".join(tabs)}<button type="button" class="print-btn" data-print>Εκτύπωση</button></div>
{_quick(quick_links(d, kind))}
{_source(d)}
<div class="pi-layout">
  {_toc(docs, kind)}
  <div class="pi-body">{"".join(_doc(doc, i, len(docs) > 1) for i, doc in enumerate(docs))}</div>
</div>
{_source(d)}
</div>
"""
    path = f"/fylla/{slug}/" if kind == "pil" else f"/fylla/{slug}/pxp/"
    return pages.layout(
        ctx,
        f"{name} · {title} · farmaka.info",
        body,
        path,
        f"{title} για το {name} (επίσημο κείμενο της EMA, στα ελληνικά), οργανωμένο σε ενότητες.",
        trail=[("Επίσημα κείμενα", "/fylla/"), (f"{name}" + ("" if kind == "pil" else " · ΠΧΠ"), path)],
    )


# --- The leaflet inside the package page --------------------------------------
#
# «Με μια ματιά» answers are picked by fixed rules from the leaflet text and shown
# word for word (cut only at a sentence or clause boundary, marked with «…»): never
# rewritten. Each answer links to its section, which is on the same page.

_SENT = re.compile(r"(?<=[.!;])\s+(?=[«(\"Α-ΩΆΈΉΊΌΎΏA-Z0-9])")
_USE = re.compile(
    r"χρησιμοποιείται|χρησιμοποιούνται|ενδείκνυται|χορηγείται\s+για|για\s+(?:τη|την)\s+θεραπεία"
)
_USE_SELF = re.compile(r"(?:χρησιμοποιείται|ενδείκνυται|χορηγείται)\s+(?:για|σε|στ|μαζί|ως|επίσης)")
_BOILER_HOW = re.compile(
    r"^(?:Πάντοτε|Πάντα)\s+να|^Εάν\s+έχετε\s+(?:αμφιβολ|περαιτέρω|οποιεσδήποτε)|^(?:Ρωτήστε|Απευθυνθείτε|Ελέγξτε\s+με)"
)
_BOILER_STORE = re.compile(
    r"παιδιά|ημερομηνία\s+λήξης|EXP|[Μμ]ην\s+πετ[άα]τε|[Ρρ]ωτήστε\s+τον\s+φαρμακοποιό|απόρριψη|απορρίμματα|λήγει"
)
_DOSE = re.compile(r"\d\s*(?:mg|g|ml|μg|mcg|IU|μονάδ|δισκ|κάψουλ|σταγ|ψεκ|εισπν|φορ)", re.I)
_VERY_COMMON = re.compile(r"πολύ\s+συχνές", re.I)
_FREQ = re.compile(r"συχνές|σπάνιες|συχνότητα|μη\s+γνωστ", re.I)
# «Φυλάσσετε (και μεταφέρετε) σε ψυγείο (2°C - 8°C)», not «Μη φυλάσσετε σε ψυγείο» and not
# «…μπορεί να αποθηκευτεί σε ψυγείο… για έως και 24 ώρες» after reconstitution
_FRIDGE_PIL = re.compile(
    r"(?<!Μη )(?<!Μην )[Φφ]υλάσσετε[^.]{0,40}?σε\s+ψυγείο\s*\(\s*2\s*°?\s*C?\s*(?:[–-]|έως)\s*8"
)
_HOW_SUBS = re.compile(r"^Εάν\s+(?:ξεχάσετε|πάρετε|χρησιμοποιήσετε|σας\s+χορηγηθεί|σταματήσετε|χάσετε)")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT.split(" ".join(text.split())) if s.strip()]


def _clip(text: str, limit: int) -> str:
    """Word-for-word, cut at a word boundary with «…» when longer than the limit."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:·-– ")
    return cut + "…"


def _item(text: str) -> str:
    """A list item reduced to its first clause: «Απώλεια μαλλιών (η πλειοψηφία…)» -> «Απώλεια μαλλιών»."""
    first = _sentences(text)[0] if text.strip() else ""
    head = re.split(r"\s+\(|:\s", first, maxsplit=1)[0].rstrip(".,;: ")
    return _clip(head if len(head) >= 3 else first, 70)


def _commas(text: str) -> list[str]:
    """Split «Zάλη, κόπωση, γρήγορος κτύπος (αίσθημα παλμών), ναυτία.» at the commas outside brackets."""
    out, depth, cur = [], 0, ""
    for ch in " ".join(text.split()).rstrip("."):
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    out.append(cur.strip())
    return [x for x in out if x]


def _section(doc: dict, num: str) -> dict | None:
    for s in doc.get("sections", []):
        if str(s.get("number")) == num:
            return s
    return None


def glance(doc: dict) -> dict:
    """The «Με μια ματιά» answers of one leaflet; a key is missing when the rule finds nothing."""
    out: dict = {}
    s1 = _section(doc, "1")
    if s1:
        blocks = s1.get("blocks", [])

        def find(rx: re.Pattern) -> tuple[int, str] | None:
            for i, b in enumerate(blocks):
                texts = [b["text"]] if b.get("type") == "paragraph" else list(b.get("items", []))
                hit = next((s for t in texts for s in _sentences(t) if rx.search(s)), None)
                if hit:
                    return i, hit
            return None

        # the medicine as the subject («Το X χρησιμοποιείται για…») before a sentence about its
        # class («…ταξάνες που χρησιμοποιούνται στον καρκίνο»)
        found = find(_USE_SELF) or find(_USE)
        for i, hit in [found] if found else []:
            items: list[str] = []
            # «Το Χ ενδείκνυται για τη θεραπεία» with the list right after it and no «:» (EOF texts)
            nxt = blocks[i + 1] if i + 1 < len(blocks) else {}
            last = _sentences(blocks[i].get("text", "") or "")[-1:] == [hit]
            opens = last and nxt.get("type") == "list" and not hit.endswith((".", ";", "!", "»", ")"))
            if hit.endswith(":") or opens:
                for nb in blocks[i + 1 :]:
                    if nb.get("type") == "subheading":
                        break
                    if nb.get("type") == "list" and not items:
                        items = [_item(x) for x in nb.get("items", [])]
                        break
                    if (
                        nb.get("type") == "paragraph"
                        and len(nb["text"]) <= 60
                        and not nb["text"].endswith(".")
                    ):
                        items.append(_item(nb["text"]))
                if not items:
                    hit = hit.rstrip(":") + "…"  # «…:» with nothing after it
            out["use"] = {"text": _clip(hit, 260), "items": items[:5], "more": max(len(items) - 5, 0)}
            break
    s2 = _section(doc, "2")
    if s2:
        heads = [b["text"].rstrip(": ") for b in s2.get("blocks", []) if b.get("type") == "subheading"]
        if heads:
            out["before"] = [(_clip(h, 60), f"fo-s2-h{k}") for k, h in enumerate(heads)][:9]
    s3 = _section(doc, "3")
    if s3:
        text = next(
            (
                s
                for b in s3.get("blocks", [])
                if b.get("type") == "paragraph"
                for s in _sentences(b["text"])
                if not _BOILER_HOW.search(s) and not s.endswith(":") and len(s.split()) >= 4
            ),
            None,
        )
        heads = [
            (b["text"].rstrip(": "), k)
            for k, b in enumerate(x for x in s3.get("blocks", []) if x.get("type") == "subheading")
        ]
        subs = [(_clip(h, 60), f"fo-s3-h{k}") for h, k in heads if _HOW_SUBS.search(h)]
        # a dose taken out of its context (indication, weight, kidney function) can mislead
        if text and not _DOSE.search(text):
            out["how"] = {"text": _clip(text, 220), "subs": subs}
        elif subs:
            out["how"] = {"text": "", "subs": subs}
    s4 = _section(doc, "4")
    if s4:
        blocks = s4.get("blocks", [])
        for i, b in enumerate(blocks):
            if b.get("type") == "list" or not _VERY_COMMON.search(b.get("text", "")):
                continue
            # the items: lines after the heading in the same block ("Πολύ συχνές (…)" + newline
            # + "Πόνος…"), the lists that follow, or one short paragraph of comma-separated items
            items = [_item(x) for x in b.get("text", "").split("\n")[1:] if x.strip()]
            for k, nb in enumerate(blocks[i + 1 :]):
                if nb.get("type") == "list":
                    items += [_item(x) for x in nb.get("items", [])]
                elif _FREQ.search(nb.get("text", "")) or nb.get("type") == "subheading":
                    break
                elif k == 0 and not items and nb.get("type") == "paragraph" and len(nb["text"]) <= 300:
                    items = [_item(x) for x in _commas(nb["text"])]
                else:
                    break
            if items:
                out["very_common"] = {"items": items[:6], "more": max(len(items) - 6, 0)}
                break
    s5 = _section(doc, "5")
    if s5:
        text = _text(s5)
        labels = []
        if _FRIDGE_PIL.search(text):
            labels.append("Ψυγείο 2–8 °C")
        if _FREEZER.search(text):
            labels.append("Κατάψυξη")
        first = next((s for s in _sentences(text) if not _BOILER_STORE.search(s)), "")
        if first or labels:
            out["storage"] = {"text": _clip(first, 200), "labels": labels}
    return out


def source_iso(d: dict) -> str:
    """The text's date as YYYY-MM-DD: the file's own date (EOF) or the EMA pdf's Last-Modified."""
    src = d.get("source") or {}
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", src.get("date") or ""):
        return src["date"]
    try:
        return parsedate_to_datetime(src["last_modified"]).date().isoformat()
    except (KeyError, TypeError, ValueError):
        return ""


def source_date(d: dict) -> str:
    """The text's date: the file's own date (EOF) or the EMA pdf's Last-Modified."""
    iso = source_iso(d)
    return date_gr(iso) if iso else ""


def source_short(d: dict) -> str:
    return (d.get("source") or {}).get("short") or "EMA"


def glance_html(g: dict, date: str, who: str = "EMA") -> str:
    if not g:
        return ""
    tiles = []
    if "use" in g:
        u = g["use"]
        items = "".join(f"<li>{e(x)}</li>" for x in u["items"])
        more = f'<li class="more">και {u["more"]} ακόμη</li>' if u["more"] else ""
        lst = f"<ul>{items}{more}</ul>" if items else ""
        tiles.append(
            f'<div class="tile"><h3><a href="#fo-s1">Για τι χρησιμοποιείται</a></h3><p>{e(u["text"])}</p>{lst}</div>'
        )
    if "how" in g:
        h = g["how"]
        subs = "".join(f'<a class="chip" href="#{e(a)}">{e(t)}</a>' for t, a in h["subs"])
        text = f"<p>{e(h['text'])}</p>" if h["text"] else ""
        subs = f'<div class="chips">{subs}</div>' if subs else ""
        tiles.append(f'<div class="tile"><h3><a href="#fo-s3">Πώς χρησιμοποιείται</a></h3>{text}{subs}</div>')
    if "before" in g:
        chips = "".join(f'<a class="chip" href="#{e(a)}">{e(t)}</a>' for t, a in g["before"])
        tiles.append(
            f'<div class="tile"><h3><a href="#fo-s2">Πριν από τη χρήση</a></h3><div class="chips">{chips}</div></div>'
        )
    if "very_common" in g:
        v = g["very_common"]
        chips = "".join(f'<span class="chip warn">{e(x)}</span>' for x in v["items"])
        more = (
            f'<p class="small">και {v["more"]} ακόμη · <a href="#fo-s4">ενότητα 4</a></p>'
            if v["more"]
            else ""
        )
        tiles.append(
            f'<div class="tile"><h3><a href="#fo-s4">Πολύ συχνές ανεπιθύμητες ενέργειες</a></h3>'
            f'<p class="small">σε περισσότερους από 1 στους 10</p><div class="chips">{chips}</div>{more}</div>'
        )
    if "storage" in g:
        s = g["storage"]
        chips = "".join(f'<span class="chip flag">{e(x)}</span>' for x in s["labels"])
        chips = f'<div class="chips">{chips}</div>' if chips else ""
        text = f"<p>{e(s['text'])}</p>" if s["text"] else ""
        tiles.append(f'<div class="tile"><h3><a href="#fo-s5">Φύλαξη</a></h3>{chips}{text}</div>')
    when = f" · {e(date)}" if date else ""
    return f"""<section class="card glance" aria-labelledby="glance-h">
      <div class="section-head"><h2 id="glance-h">Με μια ματιά</h2><span class="small">Αυτούσια από το φύλλο οδηγιών · {e(who)}{when}</span></div>
      <div class="tiles">{"".join(tiles)}</div>
    </section>"""


def _section_rows(doc: dict, prefix: str, open_first: bool) -> str:
    """Sections as <details>; a parent heading without text of its own («4. ΚΛΙΝΙΚΕΣ
    ΠΛΗΡΟΦΟΡΙΕΣ» before 4.1) is a plain heading row. A text without sections is one row."""
    rows = []
    secs = doc.get("sections", [])
    if not secs and doc.get("preamble"):
        return (
            f'<details class="lf-sec" id="{prefix}-all"{" open" if open_first else ""}><summary><span class="n"></span>'
            f'<span class="t">Όλο το κείμενο</span></summary><div class="lf-text">{_blocks(doc["preamble"], prefix + "-all")}</div></details>'
        )
    for k, s in enumerate(secs):
        num = str(s["number"])
        sid = f"{prefix}-s{num}"
        nxt = secs[k + 1]["number"] if k + 1 < len(secs) else ""
        if not s["blocks"] and str(nxt).startswith(num + "."):
            rows.append(
                f'<h3 class="lf-group" id="{e(sid)}"><span class="n">{e(num)}</span> {e(s["title"])}</h3>'
            )
            continue
        opened = " open" if open_first and k == 0 else ""
        rows.append(
            f'<details class="lf-sec" id="{e(sid)}"{opened}><summary><span class="n">{e(num)}</span>'
            f'<span class="t">{e(s["title"])}</span></summary><div class="lf-text">{_blocks(s["blocks"], sid)}</div></details>'
        )
    return "".join(rows)


def _source_line(d: dict, kind: str = "pil") -> str:
    src = (d.get("sources") or {}).get(kind) or d.get("source") or {}
    when = source_date({"source": src}) if src.get("date") else source_date(d)
    if source_short(d) == "ΕΟΦ":
        who = "Εθνικός Οργανισμός Φαρμάκων (ΕΟΦ)"
        fmt = "το αρχείο του ΕΟΦ"
    else:
        who = "Ευρωπαϊκός Οργανισμός Φαρμάκων (EMA), ελληνική έκδοση"
        fmt = "το pdf"
    return (
        f'<p class="small lf-src">Πηγή: {who}{f" · {e(when)}" if when else ""}. '
        f"Το κείμενο εξάγεται αυτόματα από {fmt}· αν κάτι διαφέρει, ισχύει {fmt}.</p>"
    )


def leaflet_html(d: dict, i: int, slug: str | None, pro_block: str = "") -> str:
    """The whole package leaflet of document i, in sections that open and close, with a table of
    contents; `pro_block` (the SmPC, or links into it) goes below it. `slug` is None for texts
    that have no pages of their own under /fylla/ (EOF): the original file is the print copy."""
    doc = d["pil"][i]
    toc = []
    for s in doc.get("sections", []):
        num = str(s["number"])
        toc.append(f'<li><a href="#fo-s{e(num)}"><span>{e(num)}</span> {e(s["title"])}</a></li>')
    if d.get("smpc") and slug:
        pro = f'<a class="lf-pro" href="/fylla/{e(slug)}/pxp/">Για επαγγελματίες: ΠΧΠ →</a>'
    elif d.get("smpc"):
        pro = '<a class="lf-pro" href="#pxp">Για επαγγελματίες: ΠΧΠ ↓</a>'
    else:
        pro = ""
    names = doc.get("products") or []
    products = " · ".join(names[:4]) + (f" και {len(names) - 4} ακόμη" if len(names) > 4 else "")
    others = (
        f' · <a href="/fylla/{e(slug)}/">άλλες μορφές ({len(d["pil"]) - 1})</a>'
        if slug and len(d["pil"]) > 1
        else ""
    )
    src = (d.get("sources") or {}).get("pil") or d.get("source") or {}
    fmt = "pdf" if str(src.get("url", "")).lower().endswith(".pdf") or source_short(d) == "EMA" else "αρχείο"
    links = [f'<a href="/fylla/{e(slug)}/">για εκτύπωση</a>'] if slug else []
    links.append(f'<a href="{e(src.get("url", ""))}" rel="noopener">{fmt} {e(source_short(d))} ↗</a>')
    toc_html = (
        f'<nav class="lf-toc" aria-label="Ενότητες φύλλου οδηγιών"><ol>{"".join(toc)}</ol>{pro}</nav>'
        if toc
        else ""
    )
    return f"""<section class="card leaflet" id="fyllo" aria-labelledby="fyllo-h">
      <div class="section-head"><h2 id="fyllo-h">Φύλλο οδηγιών χρήσης</h2>
        <span class="small">{" · ".join(links)}</span></div>
      {f'<p class="small lf-for">Κείμενο για: {e(products)}{others}</p>' if products else ""}
      <div class="lf-layout">
        {toc_html}
        <div class="lf-body">{_section_rows(doc, "fo", True)}</div>
      </div>
      {pro_block}
      {_source_line(d)}
    </section>"""


def smpc_html(d: dict) -> str:
    """The SmPC on the package page (EOF texts, which have no page of their own): sections closed,
    with links to the clinical ones."""
    docs = d.get("smpc") or []
    if not docs:
        return ""
    doc = docs[0]
    have = {str(s["number"]) for s in doc.get("sections", [])}
    chips = "".join(
        f'<a class="chip" href="#fp-s{e(num)}">{e(label)}</a>' for label, num in SMPC_QUICK if num in have
    )
    src = (d.get("sources") or {}).get("smpc") or {}
    link = (
        f' · <a href="{e(src["url"])}" rel="noopener">αρχείο {e(source_short(d))} ↗</a>'
        if src.get("url")
        else ""
    )
    return f"""<section class="card leaflet smpc" id="pxp" aria-labelledby="pxp-h">
      <div class="section-head"><h2 id="pxp-h">Περίληψη χαρακτηριστικών (ΠΧΠ)</h2>
        <span class="small">Για επαγγελματίες υγείας{link}</span></div>
      {f'<div class="chips lf-quick">{chips}</div>' if chips else ""}
      <div class="lf-body lf-full">{_section_rows(doc, "fp", False)}</div>
      {_source_line(d, "smpc")}
    </section>"""


def _norm(text: str) -> str:
    t = re.sub(r"(?<=\d),(?=\d)", ".", text)  # decimal comma only: «1,5 mg, 3 mg» keeps its list comma
    t = re.sub(r"μg|µg|μικρογραμμάρι\w*|microgram\w*", "mcg", t, flags=re.I)
    t = re.sub(r"διεθνείς\s+μονάδες", "IU", t, flags=re.I)
    t = re.sub(r"μονάδ\w*", "U", t, flags=re.I)
    return re.sub(r"\s+", "", t.upper())


# package-name words -> how the leaflet names the form (stems)
_FORM_WORDS = [
    (r"VIAL", "ΦΙΑΛΊΔΙ"),
    (r"CARTRIDGE|PENFILL", "ΦΥΣΊΓΓΙ"),
    (r"\bPEN\b|PEN\b|PENS?\b|FLEXPEN|KWIKPEN|SOLOSTAR", "ΠΈΝΑ"),
    (r"SYR|SYRINGE", "ΣΎΡΙΓΓ"),
    (r"PATCH|TTS|TRANSDERMAL", "ΈΜΠΛΑΣΤΡ"),
    (r"\bCAP|CAPS\b", "ΚΑΨΆΚΙ"),
    (r"TAB", "ΔΙΣΚΊ"),
    (r"OR\.SOL|ORAL\s*SOL", "ΠΌΣΙΜΟ"),
]


def _strength_keys(key: str) -> list[str]:
    """'150+12.5MG' -> also '150MG/12.5MG' (how leaflets write combinations); '20MG/ML' -> also '20MG'."""
    key = key.upper()
    keys = [key]
    m = re.fullmatch(r"([\d.+]+)([A-Z]+)(/.*)?", key)
    if m and "+" in m.group(1):
        keys.append("/".join(f"{x}{m.group(2)}" for x in m.group(1).split("+")))
    if "/" in key:
        keys.append(key.split("/")[0])
    return keys


def pick_doc(d_products: list[list[str]], package_name: str) -> int:
    """Which leaflet of a product belongs to a package. Each leaflet's product lines are scored:
    the package strength named (3), the same form (vial, cartridge, pen, tablet: 1), a sub-brand
    word of the package name such as PENFILL or MIX25 (1). The best score wins, the earlier
    leaflet on a tie; the first leaflet when nothing matches."""
    if len(d_products) <= 1:
        return 0
    st = strength(package_name)
    keys = _strength_keys(st[0]) if st else []
    up = package_name.upper()
    forms = [stem for rx, stem in _FORM_WORDS if re.search(rx, up)]
    words = [w for w in re.findall(r"[A-Z][A-Z0-9]{3,}", up.split(" ", 1)[1] if " " in up else "")]
    best, best_score = 0, 0
    for i, products in enumerate(d_products):
        names = [_norm(x) for x in products]
        score = 0
        if any(re.search(rf"(?<![\d.]){re.escape(k)}(?![\d])", n) for k in keys for n in names):
            score += 3
        if any(stem in n for stem in forms[:1] for n in names):
            score += 1
        if any(w in n for w in words for n in names):
            score += 1
        if score > best_score:
            best, best_score = i, score
    return best


def package_parts(lf: dict, package_name: str, failed: list | None = None) -> tuple[str, str]:
    """(«Με μια ματιά», the leaflet and SmPC sections) for a package page; empty strings without texts.
    «Με μια ματιά» only for leaflets that follow the QRD template (its rules read that template).
    With `failed`, a text the summary rules do not fit gets no summary and is listed there
    (package, error) for the build report; its full text still shows. Without it, the error raises."""
    texts = lf.get("texts") or {}
    docs = texts.get("pil") or []
    if len(docs) > 1 and any(not (x.get("sections") or x.get("preamble")) for x in docs):
        keep = [k for k, x in enumerate(docs) if x.get("sections") or x.get("preamble")]
        layouts = (texts.get("layout") or {}).get("pil") or []
        texts = {**texts, "pil": [docs[k] for k in keep]}
        if len(layouts) == len(docs):
            texts["layout"] = {**texts["layout"], "pil": [layouts[k] for k in keep]}
        docs = texts["pil"]
    slug = lf.get("slug")
    if not docs:
        return "", smpc_html(texts) if not slug else ""
    i = pick_doc([x.get("products") or [] for x in docs], package_name)
    layouts = (texts.get("layout") or {}).get("pil") or ["qrd"]
    qrd = (layouts[i] if i < len(layouts) else layouts[0]) == "qrd"
    top = ""
    if qrd:
        try:
            top = glance_html(glance(docs[i]), source_date(texts), source_short(texts))
        except Exception as err:
            if failed is None:
                raise
            failed.append((package_name, f"{type(err).__name__}: {err}"))
    if slug:
        pro = ""
        if lf.get("smpc_quick"):
            chips = "".join(
                f'<a class="chip" href="/fylla/{e(slug)}/pxp/#{e(frag)}">{e(label)}</a>'
                for label, frag in lf["smpc_quick"]
            )
            pro = f'<div class="lf-smpc"><h3>Για επαγγελματίες υγείας · ΠΧΠ</h3><div class="chips">{chips}</div></div>'
        return top, leaflet_html(texts, i, slug, pro)
    return top, leaflet_html(texts, i, None) + smpc_html(texts)


def load_eof(src: Path) -> dict[str, dict]:
    """EOF texts in sections (python -m ingest.eof_pi parse): "EOF-<product code>" -> entry.
    They get no pages under /fylla/ (the file limit of the host): leaflet and SmPC go on the
    package pages themselves."""
    index: dict[str, dict] = {}
    if not src.exists():
        return index
    for f in sorted(src.glob("[0-9]*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        if not (d.get("pil") or d.get("smpc")):
            continue
        texts = {k: d.get(k) for k in ("pil", "smpc", "source", "sources", "layout")}
        index[d["product_number"]] = {
            "slug": None,
            "name": d.get("name"),
            "pil": bool(d.get("pil")),
            "smpc": bool(d.get("smpc")),
            "flags": flags(d),
            "quick": [],
            "texts": texts,
            "smpc_quick": [],
        }
    return index


def build(ctx, src: Path, out: Path) -> dict[str, dict]:
    """Write every product's pages; return product_number -> {slug, name, pil, smpc}."""
    index: dict[str, dict] = {}
    if not src.exists():
        return index
    used: set[str] = set()
    for f in sorted(src.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        if not (d.get("pil") or d.get("smpc")):
            continue
        base = slugify(d.get("name") or d["product_number"])
        slug, i = base, 2
        while slug in used:
            slug, i = f"{base}-{i}", i + 1
        used.add(slug)
        root = out / "fylla" / slug
        root.mkdir(parents=True, exist_ok=True)
        main = "pil" if d.get("pil") else "smpc"
        (root / "index.html").write_text(_page(ctx, d, slug, main), encoding="utf-8")
        if d.get("pil") and d.get("smpc"):
            (root / "pxp").mkdir(exist_ok=True)
            (root / "pxp" / "index.html").write_text(_page(ctx, d, slug, "smpc"), encoding="utf-8")
        index[d["product_number"]] = {
            "slug": slug,
            "name": d.get("name"),
            "pil": bool(d.get("pil")),
            "smpc": bool(d.get("smpc")),
            "flags": flags(d),
            "quick": quick_links(d, "pil"),
            # for the package pages: the leaflets themselves (the SmPC stays on its own page)
            "texts": {
                "pil": d.get("pil") or [],
                "smpc": bool(d.get("smpc")),
                "source": d.get("source") or {},
            },
            "smpc_quick": quick_links(d, "smpc"),
        }
    lis = "".join(
        f'<li><a href="/fylla/{e(v["slug"])}/">{e(v["name"])}</a></li>'
        for v in sorted(index.values(), key=lambda v: (v["name"] or "").lower())
    )
    body = f"""
<div class="wrap">
<div class="page-head">
  <h1>Επίσημα κείμενα</h1>
  <p>Φύλλα οδηγιών και ΠΧΠ {len(index)} κεντρικά εγκεκριμένων φαρμάκων, από την EMA, στα ελληνικά.</p>
</div>
<ul class="az">{lis}</ul>
</div>
"""
    (out / "fylla").mkdir(parents=True, exist_ok=True)
    (out / "fylla" / "index.html").write_text(
        pages.layout(
            ctx, "Επίσημα κείμενα · farmaka.info", body, "/fylla/", "Φύλλα οδηγιών και ΠΧΠ στα ελληνικά."
        ),
        encoding="utf-8",
    )
    return index
