"""Reading pages for the official product information (EMA), from the JSON
written by `python -m ingest.ema_pi parse` (data/current/ema_pi/*.json).

Two pages per product: /fylla/<slug>/ (package leaflet, for patients) and
/fylla/<slug>/pxp/ (SmPC, for professionals). The text is shown as extracted,
never edited; the page names the EMA as the source and links the original pdf,
which prevails if anything differs.
"""

from __future__ import annotations

import json
from email.utils import parsedate_to_datetime
from pathlib import Path

from . import pages
from .pages import e
from .text import date_gr, slugify


def _blocks(blocks: list[dict]) -> str:
    out = []
    for b in blocks:
        t = b.get("type")
        if t == "paragraph":
            out.append(f"<p>{e(b['text'])}</p>")
        elif t == "subheading":
            out.append(f"<h4>{e(b['text'])}</h4>")
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
    secs = "".join(
        f'<section class="pi-sec" id="d{i}-s{e(s["number"])}"><h3><span>{e(s["number"])}.</span> {e(s["title"])}</h3>'
        f"{_blocks(s['blocks'])}</section>"
        for s in doc.get("sections", [])
    )
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
</div>
<div class="pi-tabs">{"".join(tabs)}</div>
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
