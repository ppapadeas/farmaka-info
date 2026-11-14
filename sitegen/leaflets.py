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
    )


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
