"""HTML for every page type. Plain functions returning strings; all data is
escaped with `e()`. No inline scripts or styles (the CSP forbids them)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from html import escape
from pathlib import Path
from urllib.parse import quote

from .text import date_gr, eur, num

SITE = "https://farmaka.info"
REPO = "https://github.com/ppapadeas/farmaka-info"
STATIC = Path(__file__).resolve().parent.parent / "web" / "static"

ASSET_VERSION = hashlib.sha256(
    b"".join(p.read_bytes() for p in sorted(STATIC.iterdir()) if p.is_file())
).hexdigest()[:10]

FIELD_LABELS = {
    "price_retail": "Λιανική",
    "price_wholesale": "Χονδρική",
    "price_exfactory": "Παραγωγού",
    "non_reimbursed": "Μη αποζημιούμενο",
}
LIST_LABELS = {"main": "Κύριος κατάλογος", "n3816": "Κατάλογος άρθρου 12 ν. 3816/2010"}


def e(x) -> str:
    return escape("" if x is None else str(x), quote=True)


@dataclass
class Context:
    meta: dict
    by_code: dict
    subst: dict
    slug_of: dict
    downloads: list


def layout(ctx: Context, title: str, body: str, path: str, desc: str = "", search_box: bool = True) -> str:
    mini = (
        """<form class="search search-mini" action="/anazitisi" method="get" role="search">
      <label class="sr" for="q-top">Αναζήτηση φαρμάκου</label>
      <input id="q-top" name="q" type="search" placeholder="Αναζήτηση φαρμάκου…" autocomplete="off"
        spellcheck="false" enterkeyhint="search">
      <div class="suggest" hidden></div>
    </form>"""
        if search_box
        else ""
    )
    m = ctx.meta
    return f"""<!doctype html>
<html lang="el">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<meta name="description" content="{e(desc or title)}">
<link rel="canonical" href="{SITE}{e(path)}">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="#0b6e5f">
<link rel="stylesheet" href="/static/style.css?v={ASSET_VERSION}">
<script type="module" src="/static/app.js?v={ASSET_VERSION}"></script>
</head>
<body>
<a class="skip" href="#main">Μετάβαση στο περιεχόμενο</a>
<header class="top">
  <div class="wrap top-row">
    <a class="brand" href="/" aria-label="farmaka.info, αρχική">farmaka<span>.info</span></a>
    {mini}
    <nav aria-label="Κύριο μενού"><a href="/dedomena/">Δεδομένα &amp; API</a><a href="/sxetika/">Σχετικά</a></nav>
  </div>
</header>
<main id="main" class="wrap">
{body}
</main>
<footer class="foot">
  <div class="wrap">
    <p><strong>Η πληροφορία είναι ενημερωτική και δεν υποκαθιστά γιατρό ή φαρμακοποιό.</strong>
    Η επίσημη πηγή υπερισχύει πάντα.</p>
    <p>Πηγή: Υπουργείο Υγείας (δελτία τιμών, κατάλογος αποζημιούμενων). Τελευταίο δελτίο που έχει
    ενσωματωθεί: {date_gr(m["data_published"])}. <a href="/dedomena/">Τι περιλαμβάνεται</a>.</p>
    <p>Δεδομένα <a href="/dedomena/#adeia">CC0</a> · Κώδικας <a href="{REPO}">AGPL-3.0</a> ·
    Χωρίς cookies, χωρίς διαφημίσεις, χωρίς παρακολούθηση ·
    <a href="{REPO}/issues">Αναφορά λάθους</a></p>
  </div>
</footer>
</body>
</html>
"""


def source_line(src: dict) -> str:
    if not src or not src.get("published"):
        return ""
    title = src.get("title") or "Δελτίο τιμών"
    link = f'<a href="{e(src["url"])}" rel="noopener">«{e(title)}»</a>' if src.get("url") else f"«{e(title)}»"
    art = f", άρθρο {e(src['moh_article'])}" if src.get("moh_article") else ""
    return (
        f'<p class="src">Πηγή: {e(src.get("publisher", "Υπουργείο Υγείας"))}, {link}, '
        f"δημοσίευση {date_gr(src['published'])}{art}.</p>"
    )


def subst_links(ctx: Context, names: list[str]) -> str:
    if not names:
        return '<span class="muted">—</span>'
    return " + ".join(f'<a href="/ousia/{e(ctx.slug_of[n])}/">{e(n)}</a>' for n in names)


def badges(p: dict) -> str:
    out = []
    if p["reimbursement"]:
        out.append('<span class="badge ok">Στον κατάλογο αποζημίωσης</span>')
    if p["non_reimbursed"]:
        out.append('<span class="badge warn">Μη αποζημιούμενο (σήμανση στο δελτίο τιμών)</span>')
    return f'<p class="badges">{"".join(out)}</p>' if out else ""


def pkg_row(p: dict, current: str | None = None) -> str:
    cls = ' class="current"' if p["eof_code"] == current else ""
    mark = (
        ' <span class="badge ok sm" title="Στον κατάλογο αποζημίωσης">Α</span>' if p["reimbursement"] else ""
    )
    return (
        f'<tr{cls}><td><a href="/farmako/{e(p["eof_code"])}/">{e(p["name"])}</a>{mark}</td>'
        f'<td>{e(p["mah"])}</td><td class="n">{eur(p["prices"]["retail"])}</td></tr>'
    )


def pkg_table(rows: list[dict], current: str | None = None, caption: str = "") -> str:
    cap = f"<caption>{e(caption)}</caption>" if caption else ""
    return (
        f'<div class="tw"><table class="list">{cap}<thead><tr><th scope="col">Συσκευασία</th>'
        '<th scope="col">Κάτοχος άδειας</th><th scope="col" class="n">Λιανική</th></tr></thead><tbody>'
        + "".join(pkg_row(p, current) for p in rows)
        + "</tbody></table></div>"
    )


def search_form(big: bool = True) -> str:
    return f"""<form class="search {"search-big" if big else ""}" action="/anazitisi" method="get" role="search">
  <label class="sr" for="q">Αναζήτηση φαρμάκου</label>
  <input id="q" name="q" type="search" placeholder="π.χ. apotel, παρακεταμόλη, 280…"
    autocomplete="off" spellcheck="false" enterkeyhint="search" autofocus>
  <button type="submit">Αναζήτηση</button>
  <div class="suggest" hidden></div>
</form>"""


def home(ctx: Context, history: list[dict]) -> str:
    m = ctx.meta
    recent = sorted(
        (h for h in history if h["field"] == "price_retail"),
        key=lambda h: (h["published"], h["eof_code"]),
        reverse=True,
    )[:30]
    by_art = {s["moh_article"]: s for s in m["sources"]}
    rows = []
    for h in recent:
        p = ctx.by_code.get(h["eof_code"])
        if not p:
            continue
        if h["change"] == "new":
            what = f"Νέα συσκευασία, λιανική {eur(float(h['new']))}"
        else:
            old, new = float(h["old"]), float(h["new"])
            arrow = "μείωση" if new < old else "αύξηση"
            what = f"Λιανική {eur(old)} → {eur(new)} ({arrow})"
        src = by_art.get(int(h["moh_article"]), {})
        rows.append(
            f'<tr><td class="nowrap">{date_gr(h["published"])}</td>'
            f'<td><a href="/farmako/{e(p["eof_code"])}/">{e(p["name"])}</a></td><td>{what}</td>'
            f'<td><a href="{e(src.get("url", ""))}" rel="noopener">άρθρο {e(h["moh_article"])}</a></td></tr>'
        )
    recent_html = (
        '<section><h2>Πρόσφατες αλλαγές λιανικής τιμής</h2><div class="tw"><table class="list">'
        '<thead><tr><th scope="col">Δημοσίευση</th><th scope="col">Συσκευασία</th>'
        '<th scope="col">Αλλαγή</th><th scope="col">Πηγή</th></tr></thead><tbody>'
        + "".join(rows)
        + "</tbody></table></div></section>"
        if rows
        else ""
    )
    reimb = (
        f"<li>{num(m['reimbursed_packages'], 0)} από αυτές στον κατάλογο αποζημιούμενων φαρμάκων "
        f"(αναθεώρηση {date_gr(m['reimbursement_source']['published'])})</li>"
        if m.get("reimbursement_source")
        else ""
    )
    pending = (
        f'<li class="muted">{len(m["pending_bulletins"])} μεταγενέστερα δελτία δεν έχουν ενσωματωθεί ακόμη '
        '(<a href="/dedomena/#katastasi">ποια</a>)</li>'
        if m["pending_bulletins"]
        else ""
    )
    body = f"""
<section class="hero">
  <h1>Τιμές και αποζημίωση φαρμάκων στην Ελλάδα</h1>
  <p class="lead">Όλες οι συσκευασίες του επίσημου δελτίου τιμών του Υπουργείου Υγείας, με τιμή
  παραγωγού, χονδρική και λιανική, αποζημίωση και ιστορικό. Κάθε στοιχείο δείχνει από ποιο δελτίο
  προέρχεται. Δωρεάν, χωρίς λογαριασμό, χωρίς παρακολούθηση.</p>
  {search_form()}
  <p class="hint">Εμπορική ονομασία, δραστική ουσία, εταιρεία, κωδικός ΕΟΦ ή barcode. Ελληνικά ή
  λατινικά, με ή χωρίς τόνους.</p>
</section>
<section class="stats" aria-label="Τι περιέχει">
  <ul>
    <li><strong>{num(m["packages"], 0)}</strong> συσκευασίες, <strong>{num(m["substances"], 0)}</strong>
    δραστικές ουσίες</li>
    {reimb}
    <li>Τελευταίο δελτίο που έχει ενσωματωθεί: <strong>{date_gr(m["data_published"])}</strong></li>
    {pending}
    <li>Όλα τα δεδομένα ελεύθερα για λήψη και μέσω API: <a href="/dedomena/">Δεδομένα &amp; API</a></li>
  </ul>
</section>
{recent_html}
"""
    return layout(
        ctx,
        "farmaka.info · Τιμές και αποζημίωση φαρμάκων στην Ελλάδα",
        body,
        "/",
        "Ανοιχτή βάση τιμών και αποζημίωσης φαρμάκων για την Ελλάδα, από τα επίσημα δελτία του Υπουργείου "
        "Υγείας. Δωρεάν, χωρίς λογαριασμό.",
        search_box=False,
    )


def package(ctx: Context, p: dict) -> str:
    m = ctx.meta
    pr = p["prices"]
    vat = f"{num(p['vat'] * 100, 0)}%" if p["vat"] else "—"
    atc = e(p["atc"]) if p["atc"] else '<span class="muted">δεν αναφέρεται</span>'
    prices = f"""
<section class="card">
  <h2>Τιμές</h2>
  <table class="kv">
    <tr><th scope="row">Λιανική</th><td class="n big">{eur(pr["retail"])}</td></tr>
    <tr><th scope="row">Χονδρική</th><td class="n">{eur(pr["wholesale"])}</td></tr>
    <tr><th scope="row">Παραγωγού</th><td class="n">{eur(pr["exfactory"])}</td></tr>
    <tr><th scope="row">ΦΠΑ</th><td class="n">{vat}</td></tr>
  </table>
  {source_line(p["price_source"])}
</section>"""

    if p["reimbursement"]:
        parts = []
        for r in p["reimbursement"]:
            diff = (
                r["retail_in_list"] - r["reimbursement_price"]
                if r["retail_in_list"] is not None and r["reimbursement_price"] is not None
                else None
            )
            restr = (
                '<tr><th scope="row">Περιορισμοί</th><td>'
                + "".join(f"<p>{e(x)}</p>" for x in r["restrictions"])
                + "</td></tr>"
                if r["restrictions"]
                else ""
            )
            ddd = (
                f'<tr><th scope="row">Καθορισμένη ημερήσια δόση (DDD)</th>'
                f'<td class="n">{num(r["ddd"], 3).rstrip("0").rstrip(",")} {e(r["ddd_unit"] or "")}</td></tr>'
                if r["ddd"] is not None
                else ""
            )
            ahd = (
                f'<tr><th scope="row">Ημερήσιες δόσεις στη συσκευασία</th>'
                f'<td class="n">{num(r["daily_doses_per_pack"], 2)}</td></tr>'
                if r["daily_doses_per_pack"] is not None
                else ""
            )
            khth = (
                f'<tr><th scope="row">Κόστος ημερήσιας θεραπείας</th>'
                f'<td class="n">{eur(r["daily_cost"])}</td></tr>'
                if r["daily_cost"] is not None
                else ""
            )
            diff_row = (
                f'<tr><th scope="row">Διαφορά λιανικής από τιμή αποζημίωσης</th>'
                f'<td class="n">{eur(diff)}</td></tr>'
                if diff is not None
                else ""
            )
            parts.append(
                f"""<table class="kv">
    <tr><th scope="row">Τιμή αποζημίωσης</th><td class="n big">{eur(r["reimbursement_price"])}</td></tr>
    <tr><th scope="row">Λιανική στον κατάλογο</th><td class="n">{eur(r["retail_in_list"])}</td></tr>
    {diff_row}{ddd}{ahd}{khth}
    <tr><th scope="row">Τρόπος διάθεσης</th><td>{e(r["dispensing"] or "—")}</td></tr>
    <tr><th scope="row">Ομάδα στον κατάλογο</th><td>{e(r["route_group"] or "—")}</td></tr>
    <tr><th scope="row">Κατάλογος</th><td>{e(LIST_LABELS.get(r["list"], r["list"]))}</td></tr>
    {restr}
  </table>"""
            )
        note = (
            '<p class="note">Όταν η λιανική είναι πάνω από την τιμή αποζημίωσης, τη διαφορά την πληρώνει '
            "ο ασφαλισμένος, επιπλέον της συμμετοχής του. Το ποσοστό συμμετοχής εξαρτάται από την πάθηση "
            "και δεν φαίνεται εδώ.</p>"
        )
        reimb = (
            '<section class="card"><h2>Αποζημίωση</h2>'
            + "".join(parts)
            + note
            + source_line(p["reimbursement"][0]["source"])
            + "</section>"
        )
    elif m.get("reimbursement_source"):
        rs = m["reimbursement_source"]
        reimb = (
            '<section class="card"><h2>Αποζημίωση</h2><p>Η συσκευασία δεν βρέθηκε στον Κατάλογο '
            f"Αποζημιούμενων Φαρμάκων (αναθεώρηση {date_gr(rs['published'])}). Μπορεί να είναι νεότερη "
            "του καταλόγου ή να μην αποζημιώνεται.</p>" + source_line(rs) + "</section>"
        )
    else:
        reimb = ""

    hist = p["price_history"]
    first = m["sources"][0]
    if hist:
        hrows = "".join(
            f'<tr><td class="nowrap">{date_gr(h["published"])}</td><td>{e(FIELD_LABELS.get(h["field"], h["field"]))}</td>'
            f'<td class="n">{"—" if h["old"] is None else (eur(h["old"]) if h["field"] != "non_reimbursed" else ("ναι" if h["old"] else "όχι"))}</td>'
            f'<td class="n">{"—" if h["new"] is None else (eur(h["new"]) if h["field"] != "non_reimbursed" else ("ναι" if h["new"] else "όχι"))}</td>'
            f"<td>άρθρο {e(h['moh_article'])}</td></tr>"
            for h in hist
        )
        history = (
            '<section class="card"><h2>Ιστορικό</h2><div class="tw"><table class="list"><thead><tr>'
            '<th scope="col">Δημοσίευση</th><th scope="col">Πεδίο</th><th scope="col" class="n">Πριν</th>'
            '<th scope="col" class="n">Μετά</th><th scope="col">Πηγή</th></tr></thead><tbody>'
            + hrows
            + f'</tbody></table></div><p class="src">Το ιστορικό ξεκινά από τον πλήρη κατάλογο της '
            f"{date_gr(first['published'])}.</p></section>"
        )
    else:
        history = (
            '<section class="card"><h2>Ιστορικό</h2><p>Καμία αλλαγή τιμής από τον πλήρη κατάλογο της '
            f"{date_gr(first['published'])} μέχρι το τελευταίο δελτίο που έχει ενσωματωθεί "
            f"({date_gr(m['data_published'])}).</p></section>"
        )

    same = sorted(
        (
            ctx.by_code[c]
            for c in (ctx.subst[ctx.slug_of[p["substances"][0]]]["codes"] if p["substances"] else [])
            if ctx.by_code[c]["substances"] == p["substances"]
        ),
        key=lambda x: (x["prices"]["retail"], x["name"]),
    )
    if len(same) > 1:
        shown = same[:25]
        if p not in shown:
            shown.append(p)
        more = (
            f'<p><a href="/ousia/{e(ctx.slug_of[p["substances"][0]])}/">Όλες οι {len(same)} συσκευασίες</a></p>'
            if len(same) > len(shown)
            else ""
        )
        alt = (
            '<section class="card"><h2>Άλλες συσκευασίες με τις ίδιες δραστικές ουσίες</h2>'
            '<p class="note">Ταξινόμηση κατά λιανική τιμή. Μορφή, περιεκτικότητα και μέγεθος συσκευασίας '
            "διαφέρουν· δεν σημαίνει ότι είναι εναλλάξιμα. Ρωτήστε τον φαρμακοποιό ή τον γιατρό σας.</p>"
            + pkg_table(shown, p["eof_code"])
            + more
            + "</section>"
        )
    else:
        alt = ""

    issue = quote(f"Λάθος στη συσκευασία {p['eof_code']}")
    body = f"""
<nav class="crumbs" aria-label="Διαδρομή"><a href="/">Αρχική</a> › Συσκευασία {e(p["eof_code"])}</nav>
<h1 class="pkg">{e(p["name"])}</h1>
{badges(p)}
<dl class="facts">
  <div><dt>Δραστική ουσία</dt><dd>{subst_links(ctx, p["substances"])}</dd></div>
  <div><dt>Κάτοχος άδειας κυκλοφορίας</dt><dd>{e(p["mah"]) or "—"}</dd></div>
  <div><dt>ATC</dt><dd>{atc}</dd></div>
  <div><dt>Κωδικός ΕΟΦ</dt><dd class="mono">{e(p["eof_code"])}</dd></div>
  <div><dt>Barcode</dt><dd class="mono">{e(p["barcode"])}</dd></div>
</dl>
<div class="grid">
{prices}
{reimb}
</div>
{history}
{alt}
<p class="meta-links">Σε μορφή JSON: <a href="/api/v1/packages/{e(p["eof_code"])}"><code>/api/v1/packages/{e(p["eof_code"])}</code></a>
 · <a href="{REPO}/issues/new?title={issue}">Αναφορά λάθους</a></p>
<p class="note">Για το επίσημο κείμενο του φαρμάκου (ΠΧΠ, φύλλο οδηγιών) δείτε τον
<a href="https://www.eof.gr" rel="noopener">ΕΟΦ</a>. Η ενσωμάτωσή του εδώ εκκρεμεί.</p>
"""
    subst = ", ".join(p["substances"]) or "—"
    return layout(
        ctx,
        f"{p['name']} · τιμή {eur(pr['retail'])} · farmaka.info",
        body,
        f"/farmako/{p['eof_code']}/",
        f"{p['name']} ({subst}, {p['mah']}). Λιανική {eur(pr['retail'])}, χονδρική {eur(pr['wholesale'])}, "
        f"αποζημίωση και ιστορικό τιμών, με πηγή το Υπουργείο Υγείας.",
    )


def substance(ctx: Context, s: dict) -> str:
    rows = [ctx.by_code[c] for c in s["codes"]]
    single = sorted((p for p in rows if p["substances"] == [s["name"]]), key=lambda p: p["name"])
    combo = sorted((p for p in rows if p["substances"] != [s["name"]]), key=lambda p: p["name"])
    parts = []
    if single:
        parts.append(f"<section><h2>Μόνο {e(s['name'])} ({len(single)})</h2>{pkg_table(single)}</section>")
    if combo:
        groups: dict[tuple, list] = {}
        for p in combo:
            groups.setdefault(tuple(p["substances"]), []).append(p)
        html = []
        for names, ps in sorted(groups.items()):
            html.append(f"<h3>{subst_links(ctx, list(names))} ({len(ps)})</h3>{pkg_table(ps)}")
        parts.append(
            f"<section><h2>Σε συνδυασμό με άλλες ουσίες ({len(combo)})</h2>{''.join(html)}</section>"
        )
    atcs = sorted({p["atc"] for p in rows if p["atc"]})
    body = f"""
<nav class="crumbs" aria-label="Διαδρομή"><a href="/">Αρχική</a> › Δραστική ουσία</nav>
<h1>{e(s["name"])}</h1>
<p>{len(rows)} συσκευασίες στο δελτίο τιμών περιέχουν {e(s["name"])}. ATC: {e(", ".join(atcs)) or "—"}.</p>
<p class="note">Οι λίστες δεν σημαίνουν ότι τα σκευάσματα είναι εναλλάξιμα. Ρωτήστε τον φαρμακοποιό ή τον γιατρό σας.</p>
{"".join(parts)}
{source_line(ctx.meta["sources"][-1] | {"publisher": "Υπουργείο Υγείας"})}
"""
    return layout(
        ctx,
        f"{s['name']} · συσκευασίες και τιμές · farmaka.info",
        body,
        f"/ousia/{s['slug']}/",
        f"Όλες οι συσκευασίες με {s['name']} στην Ελλάδα, με λιανική τιμή και αποζημίωση.",
    )


def _size(n: int) -> str:
    if n >= 1_000_000:
        return f"{num(n / 1_000_000, 1)} MB"
    return f"{num(n / 1000, 0)} kB"


DOWNLOAD_INFO = {
    "packages.csv": "Τρέχουσα εικόνα: μία γραμμή ανά συσκευασία, τιμές και πηγή τιμής.",
    "price_history.csv": "Κάθε αλλαγή τιμής που έχει εφαρμοστεί, με το δελτίο από το οποίο προήλθε.",
    "reimbursement.csv": "Κατάλογος αποζημιούμενων: τιμή αποζημίωσης, DDD, κόστος ημερήσιας θεραπείας, περιορισμοί.",
    "farmaka.json": "Όλα τα παραπάνω σε ένα αρχείο JSON, μία εγγραφή ανά συσκευασία.",
    "farmaka.sqlite": "Βάση SQLite με πίνακες package, price_event, reimbursement, source.",
    "sources.json": "Τα δελτία που χρησιμοποιήθηκαν, με σύνδεσμο στο Υπουργείο.",
    "build_report.json": "Αναφορά build: έλεγχοι, ουρά χειροκίνητου ελέγχου, δελτία που εκκρεμούν.",
}

PACKAGE_FIELDS = [
    ("eof_code", "Κωδικός ΕΟΦ, 9 ψηφία. Μοναδικό κλειδί συσκευασίας."),
    ("barcode", "EAN-13 με πρόθεμα 280· οι θέσεις 4–12 είναι ο κωδικός ΕΟΦ."),
    ("name", "Ονομασία όπως στο δελτίο (όνομα, μορφή, περιεκτικότητα, συσκευασία)."),
    ("atc", "Κωδικός ATC όπως στο δελτίο· σε λίγες γραμμές δεν φτάνει σε επίπεδο ουσίας."),
    ("substances", "Δραστικές ουσίες, χωρισμένες με |."),
    ("mah", "Κάτοχος άδειας κυκλοφορίας, όπως στο δελτίο."),
    ("non_reimbursed", "1 αν το δελτίο το σημειώνει ως μη αποζημιούμενο."),
    ("price_exfactory", "Τιμή παραγωγού, €."),
    ("price_wholesale", "Χονδρική τιμή, €."),
    ("price_retail", "Λιανική τιμή, €."),
    ("vat", "ΦΠΑ ως κλάσμα (0.06 = 6%)."),
    ("price_published", "Ημερομηνία δημοσίευσης του δελτίου που όρισε την τρέχουσα τιμή."),
    ("price_moh_article", "Αριθμός άρθρου του Υπουργείου για το ίδιο δελτίο."),
]


def data_page(ctx: Context) -> str:
    m = ctx.meta
    dls = "".join(
        f'<tr><td><a href="/data/{e(d["name"])}">{e(d["name"])}</a></td><td>{e(DOWNLOAD_INFO.get(d["name"], ""))}</td>'
        f'<td class="n nowrap">{_size(d["bytes"])}</td><td class="mono hash" title="{e(d["sha256"])}">{e(d["sha256"][:16])}…</td></tr>'
        for d in ctx.downloads
        if d["name"] != "SHA256SUMS"
    )
    srcs = "".join(
        f'<li>{date_gr(s["published"])}: <a href="{e(s.get("url", ""))}" rel="noopener">{e(s.get("title") or s["file"])}</a> '
        f"(άρθρο {e(s['moh_article'])}, {'πλήρης κατάλογος' if s['kind'] == 'full' else 'δελτίο αλλαγών'})</li>"
        for s in m["sources"]
    )
    if m.get("reimbursement_source"):
        rs = m["reimbursement_source"]
        srcs += (
            f'<li>{date_gr(rs["published"])}: <a href="{e(rs["url"])}" rel="noopener">{e(rs["title"])}</a> '
            f"(άρθρο {e(rs['moh_article'])}, pdf· κύριος κατάλογος και κατάλογος ν. 3816/2010)</li>"
        )
    pend = (
        "<p>Γνωστά δελτία που <strong>δεν</strong> έχουν ενσωματωθεί ακόμη:</p><ul>"
        + "".join(
            f'<li>{date_gr(x["published"])}: <a href="{e(x["url"])}" rel="noopener">{e(x["title"])}</a> '
            f"(άρθρο {e(x['moh_article'])}) · {'δεν έχει ληφθεί ακόμη' if x['reason'] == 'not downloaded yet' else 'θέλει χειροκίνητο έλεγχο'}</li>"
            for x in m["pending_bulletins"]
        )
        + "</ul><p>Οι τιμές των συσκευασιών που αλλάζουν σε αυτά τα δελτία μπορεί να μην είναι οι τρέχουσες.</p>"
        if m["pending_bulletins"]
        else "<p>Δεν υπάρχουν γνωστά δελτία που να εκκρεμούν.</p>"
    )
    fields = "".join(f"<tr><td><code>{e(k)}</code></td><td>{e(v)}</td></tr>" for k, v in PACKAGE_FIELDS)
    example = next(iter(ctx.by_code))
    body = f"""
<h1>Δεδομένα &amp; API</h1>
<p class="lead">Όλα τα δεδομένα του site είναι ελεύθερα: κατεβάστε τα, χρησιμοποιήστε τα σε δικό σας
λογισμικό, χωρίς εγγραφή και χωρίς κλειδί.</p>

<section id="katastasi"><h2>Κατάσταση ενημέρωσης</h2>
<p>Τελευταίο build: {date_gr(m["built_at"])}. Ενσωματωμένα δελτία:</p>
<ul>{srcs}</ul>
{pend}
</section>

<section id="lipseis"><h2>Λήψεις</h2>
<div class="tw"><table class="list"><thead><tr><th scope="col">Αρχείο</th><th scope="col">Περιεχόμενο</th>
<th scope="col" class="n">Μέγεθος</th><th scope="col">SHA-256</th></tr></thead><tbody>{dls}</tbody></table></div>
<p>Έλεγχος ακεραιότητας: <a href="/data/SHA256SUMS">SHA256SUMS</a>. Κωδικοποίηση UTF-8, δεκαδικά με τελεία.</p>
</section>

<section id="adeia"><h2>Άδεια</h2>
<p>Τα δεδομένα διατίθενται με <a href="https://creativecommons.org/publicdomain/zero/1.0/deed.el" rel="noopener">CC0 1.0</a>:
χωρίς περιορισμούς, ούτε υποχρέωση αναφοράς. Μια αναφορά στο farmaka.info και στο Υπουργείο Υγείας ως πηγή
βοηθά τους χρήστες σας να ελέγχουν την προέλευση. Ο κώδικας είναι ελεύθερος με
<a href="{REPO}/blob/main/LICENSE">AGPL-3.0</a>.</p>
</section>

<section id="pedia"><h2>Πεδία του packages.csv</h2>
<div class="tw"><table class="list"><thead><tr><th scope="col">Πεδίο</th><th scope="col">Περιγραφή</th></tr></thead>
<tbody>{fields}</tbody></table></div>
<p>Τα ονόματα των πεδίων είναι σταθερά. Αλλαγή σχήματος σημαίνει νέο <code>schema_version</code>
(τρέχον: {e(m["schema_version"])}).</p>
</section>

<section id="api"><h2>API</h2>
<p>Read-only, JSON, χωρίς κλειδί, με CORS ανοιχτό για όλους. Παρακαλούμε για λογική χρήση· για μαζική
επεξεργασία προτιμήστε τις λήψεις.</p>
<div class="tw"><table class="list"><thead><tr><th scope="col">Αίτημα</th><th scope="col">Επιστρέφει</th></tr></thead><tbody>
<tr><td><a href="/api/v1/packages/{e(example)}"><code>GET /api/v1/packages/{{κωδικός ΕΟΦ}}</code></a></td><td>Μία συσκευασία: τιμές, πηγή, ιστορικό, αποζημίωση.</td></tr>
<tr><td><a href="/api/v1/barcode/{e(ctx.by_code[example]["barcode"])}"><code>GET /api/v1/barcode/{{EAN-13}}</code></a></td><td>Το ίδιο, με barcode.</td></tr>
<tr><td><a href="/api/v1/search?q=apotel"><code>GET /api/v1/search?q=…&amp;limit=…</code></a></td><td>Αναζήτηση (όνομα, ουσία, εταιρεία, κωδικός). Έως 200 αποτελέσματα.</td></tr>
<tr><td><a href="/api/v1/substances"><code>GET /api/v1/substances</code></a></td><td>Όλες οι δραστικές ουσίες με τους κωδικούς των συσκευασιών τους.</td></tr>
<tr><td><a href="/api/v1/meta"><code>GET /api/v1/meta</code></a></td><td>Πότε ενημερώθηκε, από ποια δελτία, τι εκκρεμεί.</td></tr>
</tbody></table></div>
</section>

<section id="kena"><h2>Γνωστά κενά</h2>
<ul>
<li>Το ιστορικό τιμών ξεκινά από τον πλήρη κατάλογο της {date_gr(m["sources"][0]["published"])}· το παλαιότερο ιστορικό θα προστεθεί σταδιακά.</li>
<li>Ως ημερομηνία τιμής χρησιμοποιείται η δημοσίευση του δελτίου. Η ημερομηνία ισχύος μπορεί να διαφέρει.</li>
<li>Οι αποσύρσεις συσκευασιών φαίνονται μόνο στον επόμενο πλήρη κατάλογο.</li>
<li>Φάρμακα που δεν υπάρχουν στο δελτίο τιμών (π.χ. πολλά μη συνταγογραφούμενα με ελεύθερη τιμή) δεν εμφανίζονται.</li>
<li>Οι τροποποιήσεις και ορθές επαναλήψεις αποφάσεων δεν εφαρμόζονται αυτόματα· ελέγχονται χειροκίνητα.</li>
<li>Οι σημαίες «Σ» και «Τ» του καταλόγου αποζημίωσης δημοσιεύονται όπως είναι· η σημασία τους δεν έχει επιβεβαιωθεί.</li>
<li>Δεν περιλαμβάνονται ακόμη ΠΧΠ και φύλλα οδηγιών: τα στοιχεία του ΕΟΦ θα έρθουν με επίσημο αίτημα, όχι με scraping.</li>
</ul>
<p>Ο κώδικας του pipeline και τα tests: <a href="{REPO}">{REPO.removeprefix("https://")}</a>.</p>
</section>
"""
    return layout(
        ctx,
        "Δεδομένα & API · farmaka.info",
        body,
        "/dedomena/",
        "Ανοιχτά δεδομένα τιμών και αποζημίωσης φαρμάκων (CC0) και API χωρίς κλειδί.",
    )


def about(ctx: Context) -> str:
    body = f"""
<h1>Σχετικά</h1>
<section><h2>Τι είναι</h2>
<p>Το farmaka.info είναι μια ελεύθερη, ανοιχτή βάση πληροφοριών για τα φάρμακα στην Ελλάδα. Συγκεντρώνει τα
επίσημα δελτία τιμών και τον κατάλογο αποζημιούμενων φαρμάκων του Υπουργείου Υγείας, τα ενώνει ανά
συσκευασία και τα δημοσιεύει ως ιστοσελίδες, αρχεία και API.</p>
<p>Η πληροφορία αυτή είναι ήδη δημόσια, αλλά σκόρπια σε δεκάδες αρχεία xlsx και pdf. Εδώ τη βρίσκετε σε
ένα σημείο, με το ιστορικό της και με σύνδεσμο στο έγγραφο από το οποίο προέρχεται κάθε τιμή.</p>
</section>
<section><h2>Για ποιον</h2>
<p>Για φαρμακοποιούς, γιατρούς, νοσηλευτές, φοιτητές, ασθενείς, δημοσιογράφους, και για όσους φτιάχνουν
λογισμικό υγείας και χρειάζονται αξιόπιστα, ανοιχτά δεδομένα.</p>
</section>
<section><h2>Τι δεν είναι</h2>
<p>Δεν είναι ιατρική συμβουλή. Δεν προτείνει θεραπεία και δεν ελέγχει αλληλεπιδράσεις για συγκεκριμένο
ασθενή. Για οτιδήποτε αφορά την υγεία σας, ρωτήστε τον γιατρό ή τον φαρμακοποιό σας. Αν κάτι εδώ διαφέρει
από την επίσημη πηγή, ισχύει η επίσημη πηγή.</p>
</section>
<section><h2>Πηγές</h2>
<ul>
<li>Υπουργείο Υγείας, <a href="https://www.moh.gov.gr/articles/times-farmakwn/deltia-timwn" rel="noopener">Δελτία Τιμών Φαρμάκων</a>.</li>
<li>Υπουργείο Υγείας, <a href="https://www.moh.gov.gr/articles/times-farmakwn/epitroph-aksiologhshs-kai-apozhmiwshs-farmakwn" rel="noopener">Κατάλογος Αποζημιούμενων Φαρμάκων</a>.</li>
</ul>
<p>Χρησιμοποιούμε μόνο δημόσιες πηγές ή πηγές με ρητή άδεια. Δεν αντιγράφουμε δεδομένα από ιδιωτικές βάσεις.
Τα στοιχεία του ΕΟΦ (μητρώο, ΠΧΠ, φύλλα οδηγιών) θα προστεθούν όταν διατεθούν με επίσημο αίτημα.</p>
</section>
<section><h2>Ιδιωτικότητα</h2>
<p>Δεν υπάρχουν λογαριασμοί, cookies, διαφημίσεις, εργαλεία analytics ή scripts τρίτων. Οι αναζητήσεις
γίνονται μέσα στον browser σας. Το site φιλοξενείται στο Cloudflare, που κρατά τεχνικά logs για την
ασφάλεια της υπηρεσίας· εμείς δεν τα χρησιμοποιούμε για να παρακολουθούμε επισκέπτες.</p>
</section>
<section><h2>Ποιος το φτιάχνει</h2>
<p>Ανεξάρτητο project του Πιέρρου Παπαδέα. Ο κώδικας είναι ανοιχτός
(<a href="{REPO}">{REPO.removeprefix("https://")}</a>, AGPL-3.0) και τα δεδομένα ελεύθερα (CC0).
Συνεισφορές είναι ευπρόσδεκτες.</p>
</section>
<section><h2>Επικοινωνία</h2>
<p>Βρήκατε λάθος; Ανοίξτε ένα <a href="{REPO}/issues">issue στο GitHub</a> με τον κωδικό ΕΟΦ της
συσκευασίας και, αν γίνεται, το έγγραφο του Υπουργείου που δείχνει τη σωστή τιμή. Κάθε διόρθωση
επαληθεύεται από την πηγή.</p>
</section>
"""
    return layout(
        ctx,
        "Σχετικά · farmaka.info",
        body,
        "/sxetika/",
        "Τι είναι το farmaka.info, από πού έρχονται τα δεδομένα, ποιος το φτιάχνει.",
    )


def not_found(ctx: Context) -> str:
    body = """
<h1>Η σελίδα δεν βρέθηκε</h1>
<p>Ίσως η διεύθυνση άλλαξε ή η συσκευασία δεν υπάρχει πια στο δελτίο τιμών. Δοκιμάστε μια αναζήτηση:</p>
""" + search_form()
    return layout(ctx, "Δεν βρέθηκε · farmaka.info", body, "/404.html", search_box=False)


def search_template(ctx: Context) -> str:
    """Filled in by web/worker.js for /anazitisi (works without JavaScript)."""
    body = """
<h1>Αναζήτηση</h1>
<form class="search search-big" action="/anazitisi" method="get" role="search">
  <label class="sr" for="q">Αναζήτηση φαρμάκου</label>
  <input id="q" name="q" type="search" value="{{QUERY}}" autocomplete="off" spellcheck="false" enterkeyhint="search">
  <button type="submit">Αναζήτηση</button>
  <div class="suggest" hidden></div>
</form>
{{RESULTS}}
"""
    return layout(ctx, "Αναζήτηση · farmaka.info", body, "/anazitisi", search_box=False)


HEADERS = """/*
  Content-Security-Policy: default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin
  Permissions-Policy: camera=(self), geolocation=(), microphone=(), interest-cohort=()
  Cross-Origin-Opener-Policy: same-origin
/static/*
  Cache-Control: public, max-age=86400
/data/*
  Access-Control-Allow-Origin: *
  Cache-Control: public, max-age=3600
/api/*
  Access-Control-Allow-Origin: *
/search-index.tsv
  Cache-Control: public, max-age=3600
/_templates/*
  X-Robots-Tag: noindex
"""


def webmanifest() -> str:
    return json.dumps(
        {
            "name": "farmaka.info",
            "short_name": "farmaka",
            "description": "Τιμές και αποζημίωση φαρμάκων στην Ελλάδα",
            "lang": "el",
            "start_url": "/",
            "display": "standalone",
            "background_color": "#ffffff",
            "theme_color": "#0b6e5f",
            "icons": [{"src": "/favicon.svg", "sizes": "any", "type": "image/svg+xml"}],
        },
        ensure_ascii=False,
        indent=1,
    )
