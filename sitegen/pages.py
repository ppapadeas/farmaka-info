"""HTML for every page type. Plain functions returning strings; all data is
escaped with `e()`. No inline scripts or styles (the CSP forbids them); charts
are inline SVG drawn with presentation attributes only."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from html import escape
from pathlib import Path
from urllib.parse import quote

from . import changes
from .drug import (
    FORM_FAMILIES,
    dispensing_label,
    form_kind,
    inn_label,
    pack_count,
    short_mah,
    split_name,
    strength,
    unit_count,
)
from .text import date_gr, eur, num

SITE = "https://farmaka.info"
REPO = "https://github.com/ppapadeas/farmaka-info"
STATIC = Path(__file__).resolve().parent.parent / "web" / "static"

# Same value on Windows (CRLF checkout) and Linux (LF): otherwise every deploy from
# another machine changes every page and re-uploads all of them.
ASSET_VERSION = hashlib.sha256(
    b"".join(p.read_bytes().replace(b"\r\n", b"\n") for p in sorted(STATIC.iterdir()) if p.is_file())
).hexdigest()[:10]

LIST_LABELS = {"main": "Κύριος κατάλογος", "n3816": "Κατάλογος ν. 3816/2010"}
EXAMPLES = ["παρακεταμόλη", "Lipitor", "μετφορμίνη", "2802342802025"]

LOGO = (
    '<svg width="28" height="28" viewBox="0 0 28 28" aria-hidden="true"><rect width="28" height="28" rx="8" '
    'fill="#0a6b5b"/><path d="M11.5 7h5v4.5H21v5h-4.5V21h-5v-4.5H7v-5h4.5z" fill="#fff"/></svg>'
)
ICON_SEARCH = (
    '<svg width="{s}" height="{s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>'
)


def e(x) -> str:
    return escape("" if x is None else str(x), quote=True)


@dataclass
class Context:
    meta: dict
    by_code: dict
    subst: dict
    slug_of: dict
    downloads: list
    equiv: dict = field(default_factory=dict)  # eof_code -> codes with same substances, strength, form
    leaflets: dict = field(default_factory=dict)  # EMA product number -> {slug, name, pil, smpc}
    compared: dict = field(default_factory=dict)  # eof_code -> Equivalents | None (cache of comparable())
    glance_failed: list = field(default_factory=list)  # «Με μια ματιά» left out: (package, error)


def sname(ctx: Context, inn: str) -> str:
    """Display name of a substance: Greek when known (Wikidata), else the INN."""
    slug = ctx.slug_of.get(inn)
    el = ctx.subst[slug].get("name_el") if slug else None
    return el or inn_label(inn)


def pct(new: float, old: float) -> str:
    if not old:
        return ""
    v = (new - old) / old * 100
    return f"{'−' if v < 0 else '+'}{num(abs(v), 0)}%"


def name_html(name: str, extra: str = "") -> str:
    brand, rest = split_name(name)
    tail = " · ".join(x for x in (rest, extra) if x)
    return f"<b>{e(brand)}</b> <span>{e(tail)}</span>" if tail else f"<b>{e(brand)}</b>"


def layout(
    ctx: Context, title: str, body: str, path: str, desc: str = "", search_box: bool = True, nav: str = ""
) -> str:
    mini = (
        f"""<form class="search search-mini" action="/anazitisi" method="get" role="search">
      {ICON_SEARCH.format(s=18)}
      <label class="sr" for="q-top">Αναζήτηση φαρμάκου</label>
      <input id="q-top" name="q" type="search" placeholder="Αναζήτηση" autocomplete="off" spellcheck="false" enterkeyhint="search">
      <div class="suggest" hidden></div>
    </form>"""
        if search_box
        else ""
    )

    def cur(n: str) -> str:
        return ' aria-current="page"' if n == nav else ""

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
<link rel="alternate" type="application/rss+xml" title="farmaka.info · Τι άλλαξε" href="/allages/rss.xml">
<link rel="apple-touch-icon" href="/static/apple-touch-icon.png">
<meta name="theme-color" content="#0a6b5b">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="farmaka">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<link rel="preload" href="/static/inter-latin.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="/static/style.css?v={ASSET_VERSION}">
<link rel="stylesheet" href="/static/scan.css?v={ASSET_VERSION}">
<script type="module" src="/static/app.js?v={ASSET_VERSION}"></script>
</head>
<body>
<a class="skip" href="#main">Μετάβαση στο περιεχόμενο</a>
<header class="top">
  <div class="wrap">
    <a class="brand" href="/" aria-label="farmaka.info, αρχική">{LOGO}<span>farmaka<span class="tld">.info</span></span></a>
    {mini}
    <nav aria-label="Κύριο μενού"><a href="/dedomena/"{cur("data")}>Δεδομένα</a><a class="opt" href="/dedomena/#api">API</a><a href="/sxetika/"{cur("about")}>Σχετικά</a></nav>
  </div>
</header>
<main id="main">
{body}
</main>
<footer class="foot">
  <div class="wrap foot-grid">
    <div class="foot-about">
      <a class="brand" href="/">{LOGO}<span>farmaka<span class="tld">.info</span></span></a>
      <p>Ενημερωτικό· δεν αντικαθιστά γιατρό ή φαρμακοποιό. Πηγές: Υπουργείο Υγείας, ΕΟΦ, EMA.</p>
    </div>
    <nav class="foot-links" aria-label="Υποσέλιδο">
      <a href="/ousies/">Δραστικές ουσίες</a>
      <a href="/elleipseis/">Ελλείψεις</a>
      <a href="/allages/">Τι άλλαξε</a>
      <a href="/fylla/">Φύλλα οδηγιών</a>
      <a href="/dedomena/">Δεδομένα &amp; API</a>
      <a href="/sxetika/">Σχετικά</a>
      <a href="{REPO}">Κώδικας</a>
      <a href="{REPO}/issues">Αναφορά λάθους</a>
    </nav>
    <div class="foot-app" hidden>
      <p><strong>Εφαρμογή</strong>Στην οθόνη σας, ανοίγει και χωρίς σύνδεση.</p>
      <button type="button" class="install">Εγκατάσταση</button>
    </div>
  </div>
  <div class="wrap foot-legal">Δεδομένα CC0 · Κώδικας AGPL-3.0 · Χωρίς cookies και παρακολούθηση · <a class="egg" href="/perierga/" title="Περίεργα από τα δεδομένα">περίεργα</a></div>
</footer>
</body>
</html>
"""


def search_form(value: str = "", autofocus: bool = True) -> str:
    af = " autofocus" if autofocus else ""
    return f"""<form class="search search-big" action="/anazitisi" method="get" role="search">
  {ICON_SEARCH.format(s=22)}
  <label class="sr" for="q">Αναζήτηση φαρμάκου</label>
  <input id="q" name="q" type="search" value="{e(value)}" placeholder="Όνομα, δραστική ουσία ή barcode" autocomplete="off" spellcheck="false" enterkeyhint="search"{af}>
  <button type="submit"><span class="btn-t">Αναζήτηση</span>{ICON_SEARCH.format(s=22)}</button>
  <div class="suggest" hidden></div>
</form>"""


# ---------------------------------------------------------------- home


def home(ctx: Context, history: list[dict]) -> str:
    m = ctx.meta
    by_art = {s["moh_article"]: s for s in m["sources"]}
    changes = sorted(
        (h for h in history if h["field"] == "price_retail" and h["change"] in ("new", "update")),
        key=lambda h: (h["published"], h["change"] == "update", h["eof_code"]),
        reverse=True,
    )[:8]
    rows = []
    for h in changes:
        p = ctx.by_code.get(h["eof_code"])
        if not p:
            continue
        new = float(h["new"])
        if h["change"] == "update" and h["old"] not in ("", None):
            old = float(h["old"])
            prices = f'<span class="old">{eur(old)}</span><span class="new">{eur(new)}</span>'
            pill = f'<span class="pill {"down" if new < old else "up"}">{pct(new, old)}</span>'
        else:
            prices = f'<span class="new">{eur(new)}</span>'
            pill = '<span class="pill new">Νέο</span>'
        src = by_art.get(int(h["moh_article"]), {})
        rows.append(
            f'<li><span class="d" title="{e(src.get("title", ""))}">{date_gr(h["published"])[:5]}</span>'
            f'<a class="nm" href="/farmako/{e(p["eof_code"])}/">{name_html(p["name"])}</a>{prices}{pill}</li>'
        )
    recent = (
        '<section class="wrap narrow recent"><div class="section-head"><h2>Πρόσφατες αλλαγές</h2>'
        '<a href="/allages/">Όλες οι αλλαγές</a></div>'
        f'<ul class="changes">{"".join(rows)}</ul></section>'
        if rows
        else ""
    )
    examples = "".join(f'<a class="chip" href="/anazitisi?q={quote(x)}">{e(x)}</a>' for x in EXAMPLES)
    reimb = m.get("reimbursed_packages") or 0
    pending = (
        f'<p class="pending">Τελευταίο δελτίο {date_gr(m["data_published"])} · '
        f'<a href="/dedomena/#katastasi">{len(m["pending_bulletins"])} νεότερα σε αναμονή</a></p>'
        if m["pending_bulletins"]
        else f'<p class="pending">Τελευταίο δελτίο {date_gr(m["data_published"])}</p>'
    )
    la = (m.get("shortages") or {}).get("limited_availability") or {}
    shortage_note = ""
    if la.get("packages"):
        shortage_note = (
            f'<p class="pending"><a href="/elleipseis/">{num(la["packages"], 0)} φάρμακα σε περιορισμένη '
            f"διαθεσιμότητα</a> · λίστα ΕΟΦ {date_gr(la['published'])}</p>"
        )
    body = f"""
<section class="wrap narrow hero">
  <h1>Πληροφορίες φαρμάκων στην Ελλάδα</h1>
  <p class="sub">Τιμές, αποζημίωση, δραστικές ουσίες και κωδικοί, από τις επίσημες πηγές. Ανοιχτά για όλους.</p>
  {search_form()}
  <div class="chips">{examples}</div>
</section>
<section class="wrap narrow" aria-label="Τι περιέχει">
  <div class="stats">
    <div><strong>{num(m["packages"], 0)}</strong><span>συσκευασίες</span></div>
    <a class="stat-link" href="/ousies/"><strong>{num(m["substances"], 0)}</strong><span>δραστικές ουσίες</span></a>
    <div><strong>{num(reimb, 0)}</strong><span>αποζημιώνονται</span></div>
  </div>
  {pending}
  {shortage_note}
</section>
{recent}
"""
    return layout(
        ctx,
        "farmaka.info · Πληροφορίες φαρμάκων στην Ελλάδα",
        body,
        "/",
        "Ανοιχτή βάση πληροφοριών για τα φάρμακα στην Ελλάδα: τιμές, αποζημίωση, δραστικές ουσίες και κωδικοί, "
        "από τις επίσημες πηγές. Δωρεάν, χωρίς λογαριασμό.",
        search_box=False,
    )


# ---------------------------------------------------------------- package


def price_series(p: dict, start: str) -> list[tuple[str, float]]:
    """(date, retail) points from the package's history; the first point is the
    earliest price we know (the oldest applied full list if nothing earlier)."""
    ev = sorted((h for h in p["price_history"] if h["field"] == "price_retail"), key=lambda h: h["published"])
    pts: list[tuple[str, float]] = []
    for h in ev:
        if h["change"] == "withdrawn" or not h["new"]:  # a few old lists print 0 as a price
            continue
        if not pts and h["change"] == "update" and h["old"] is not None:
            pts.append((start, h["old"]))
        pts.append((h["published"], h["new"]))
    if not pts:
        pts = [(start, p["prices"]["retail"])]
    return pts


def sparkline(pts: list[tuple[str, float]], end: str) -> str:
    w, h, pad = 600.0, 64.0, 8.0
    d0 = date.fromisoformat(pts[0][0])
    span = max((date.fromisoformat(end) - d0).days, 1)
    lo, hi = min(v for _, v in pts), max(v for _, v in pts)

    def x(d: str) -> float:
        return round(min(max((date.fromisoformat(d) - d0).days / span, 0), 1) * w, 1)

    def y(v: float) -> float:
        if hi == lo:
            return h / 2
        return round(pad + (hi - v) / (hi - lo) * (h - 2 * pad), 1)

    path = f"M0 {y(pts[0][1])}"
    for d, v in pts[1:]:
        path += f" H{x(d)} V{y(v)}"
    path += f" H{w:g}"
    area = path + f" V{h:g} H0 Z"
    return (
        f'<svg class="spark" viewBox="0 0 {w:g} {h:g}" preserveAspectRatio="none" aria-hidden="true">'
        f'<path class="area" d="{area}"/><path class="line" d="{path}" vector-effect="non-scaling-stroke"/></svg>'
    )


def history_card(ctx: Context, p: dict) -> str:
    m = ctx.meta
    start = m["sources"][0]["published"]
    end = m["built_at"][:10]
    pts = price_series(p, start)
    first, last = pts[0], pts[-1]
    changes = sorted(
        (h for h in p["price_history"] if h["field"] == "price_retail" and h["change"] in ("update", "new")),
        key=lambda h: h["published"],
        reverse=True,
    )
    if len(pts) > 1 and first[1] != last[1]:
        summary = (
            f"{eur(first[1])} → <b>{eur(last[1])}</b> "
            f'<span class="pill {"down" if last[1] < first[1] else "up"}">{pct(last[1], first[1])}</span>'
        )
    elif changes and changes[-1]["change"] == "new":
        summary = f"Νέα συσκευασία από {date_gr(changes[-1]['published'])}"
    else:
        summary = f"Σταθερή από {date_gr(first[0])}"
    url_of = {s["moh_article"]: s.get("url") for s in m["sources"]}
    events = "".join(
        f'<li><span class="num">{date_gr(h["published"])}</span><span>'
        + (
            f'<span class="num">{eur(h["old"])}</span> → <b class="num">{eur(h["new"])}</b>'
            if h["change"] == "update" and h["old"] is not None
            else f'νέα συσκευασία, <b class="num">{eur(h["new"])}</b>'
        )
        + f'</span><a href="{e(url_of.get(h["moh_article"], ""))}" rel="noopener">δελτίο ↗</a></li>'
        for h in changes
    )
    return f"""<section class="card">
  <div class="reimb-head"><h2>Ιστορικό τιμής</h2><span>{summary}</span></div>
  {sparkline(pts, end)}
  <div class="spark-axis"><span>{date_gr(first[0])}</span><span>σήμερα</span></div>
  {f'<ul class="hist">{events}</ul>' if events else ""}
</section>"""


def _bar(covered: float, label: str) -> str:
    c = round(max(min(covered, 1), 0) * 100, 1)
    return (
        f'<svg class="bar" viewBox="0 0 100 12" preserveAspectRatio="none" role="img" aria-label="{e(label)}">'
        f'<rect class="a" width="{c:g}" height="12"/><rect class="b" x="{c:g}" width="{100 - c:g}" height="12"/></svg>'
    )


def reimbursement_card(ctx: Context, p: dict) -> str:
    m = ctx.meta
    if not p["reimbursement"]:
        if not m.get("reimbursement_source"):
            return ""
        rs = m["reimbursement_source"]
        return (
            '<section class="card"><h2>Αποζημίωση</h2><p class="small">Δεν περιλαμβάνεται στον κατάλογο '
            f"αποζημιούμενων φαρμάκων της {date_gr(rs['published'])}.</p></section>"
        )
    r = p["reimbursement"][0]
    # Compare with today's retail price: a price change after the catalogue
    # (e.g. a later price cut) must not show a gap that no longer exists.
    retail = p["prices"]["retail"]
    rp = r["reimbursement_price"]
    diff = max(retail - rp, 0)
    stale = ""
    if r["retail_in_list"] is not None and abs(r["retail_in_list"] - retail) > 0.004:
        stale = (
            f'<p class="small">Ο κατάλογος γράφει λιανική {eur(r["retail_in_list"])}· '
            f"η τρέχουσα είναι {eur(retail)}.</p>"
        )
    per_day = []
    if r["daily_cost"] is not None:
        per_day.append(f"{eur(r['daily_cost'])} / ημέρα")
    if r["daily_doses_per_pack"] is not None:
        n = r["daily_doses_per_pack"]
        per_day.append(f"{num(n, 0) if n == int(n) else num(n, 2)} ημερήσιες δόσεις")
    head = f"<span>{' · '.join(per_day)}</span>" if per_day else ""
    if diff > 0.004:
        bar = _bar(rp / retail, f"Από τα {eur(retail)}: {eur(rp)} τιμή αποζημίωσης, {eur(diff)} διαφορά")
        legend = f"""<div class="legend">
    <div><i class="a"></i><p><strong>{eur(rp)}</strong><span>τιμή αποζημίωσης</span></p></div>
    <div><i class="b"></i><p><strong>{eur(diff)}</strong><span>διαφορά, την πληρώνει ο ασθενής</span></p></div>
  </div>"""
    else:
        bar = _bar(1, "Η τιμή αποζημίωσης καλύπτει όλη τη λιανική")
        legend = f"""<div class="legend">
    <div><i class="a"></i><p><strong>{eur(rp)}</strong><span>τιμή αποζημίωσης· καλύπτει όλη τη λιανική</span></p></div>
  </div>"""
    restr = (
        '<details class="restr"><summary>Περιορισμοί αποζημίωσης</summary>'
        + "".join(f"<p>{e(x)}</p>" for x in r["restrictions"])
        + "</details>"
        if r["restrictions"]
        else ""
    )
    return f"""<section class="card">
  <div class="reimb-head"><h2>Αποζημίωση</h2>{head}</div>
  {bar}
  {legend}
  {pay_table(ctx, p)}
  {stale}
  {restr}
</section>"""


RATES = (0, 10, 25)  # participation, % (the prescription says which applies)


def patient_cost(retail: float, rp: float, rate: int) -> float:
    """Indicative amount the patient pays for one pack at `rate`% participation (Δ-22):
    the participation on what the reimbursement covers (the reimbursement price, or
    the retail price when that is lower) plus the part of the retail price above the
    reimbursement price. Rounded to the cent, half up."""
    r, b = Decimal(str(retail)), Decimal(str(rp))
    cost = Decimal(rate) / 100 * min(b, r) + max(r - b, Decimal(0))
    return float(cost.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _costs(q: dict) -> list[float]:
    rp = q["reimbursement"][0]["reimbursement_price"]
    return [patient_cost(q["prices"]["retail"], rp, x) for x in RATES]


def cheaper_reimbursed(ctx: Context, p: dict) -> dict | None:
    """The reimbursed equivalent (Δ-16, on no EOF shortage list) that costs the patient
    least at 25%, among those that cost no more than `p` at any rate and at least 0.5%
    less at one; per unit when the unit counts are known."""
    eq = comparable(ctx, p)
    if not eq or not p["reimbursement"]:
        return None

    def per_unit(q: dict) -> list[float]:
        n = eq.counts[q["eof_code"]] if eq.per_unit else 1
        return [c / n for c in _costs(q)]

    mine = per_unit(p)
    best, best_key = None, None
    for q in eq.rows:
        if q is p or not q["reimbursement"] or in_shortage(q):
            continue
        c = per_unit(q)
        if any(a > b + 1e-9 for a, b in zip(c, mine)) or not any(a < b * 0.995 for a, b in zip(c, mine)):
            continue
        k = (c[2], c[1], c[0], eq.key(q), q["name"])
        if best_key is None or k < best_key:
            best, best_key = q, k
    return best


def pay_table(ctx: Context, p: dict) -> str:
    """«Πόσο θα πληρώσω;»: a static table for each participation rate, for this package
    and, if it costs less, the cheapest reimbursed equivalent (Δ-22)."""
    r = p["reimbursement"][0]
    q = cheaper_reimbursed(ctx, p)
    cols = [p] + ([q] if q else [])
    costs = [_costs(x) for x in cols]
    sizes = [unit_count(x["name"]) or pack_count(x["name"]) for x in cols]
    head = '<th scope="col">Συμμετοχή</th>'
    for x, n in zip(cols, sizes):
        label = (
            "Αυτό" if x is p else f'<a href="/farmako/{e(x["eof_code"])}/">{e(split_name(x["name"])[0])}</a>'
        )
        head += f'<th scope="col">{label}{f" <span>×{n}</span>" if n else ""}</th>'
    body = "".join(
        f'<tr><th scope="row">{x}%</th>{"".join(f"<td>{eur(c[i])}</td>" for c in costs)}</tr>'
        for i, x in enumerate(RATES)
    )
    notes = []
    if q:
        notes.append("Δίπλα, το φθηνότερο αποζημιούμενο ισοδύναμο χωρίς ένδειξη έλλειψης.")
        if sizes[0] != sizes[1]:
            notes.append("Διαφορετικό μέγεθος συσκευασίας· η σύγκριση έγινε ανά τεμάχιο.")
    issue = quote(f"Συμμετοχή στη συσκευασία {p['eof_code']}")
    notes.append(
        "Ενδεικτικά: η συμμετοχή επί της τιμής αποζημίωσης (ή της λιανικής, αν είναι χαμηλότερη), συν τη "
        "διαφορά. Το ποσοστό το ορίζει η συνταγή, ανάλογα με την πάθηση· το τελικό ποσό το λέει ο φαρμακοποιός. "
        f'Πληρώσατε άλλο; <a href="{REPO}/issues/new?title={issue}">Πείτε μας</a>.'
    )
    when = date_gr(p["price_source"]["published"])
    if q and q["price_source"]["published"] != p["price_source"]["published"]:
        when += f" ({e(split_name(q['name'])[0])}: {date_gr(q['price_source']['published'])})"
    lst = "" if r["list"] == "main" else f", {e(LIST_LABELS.get(r['list'], r['list']))}"
    src = (
        f"Λιανική από το δελτίο τιμών {when}· τιμή αποζημίωσης από τον κατάλογο "
        f"{date_gr(r['source']['published'])}{lst}."
    )
    return f"""<div class="pay">
    <h3>Πόσο θα πληρώσω;</h3>
    <table class="pay-t"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>
    {"".join(f'<p class="small">{x}</p>' for x in notes)}
    <p class="small">{src}</p>
  </div>"""


EOF_LISTS = ("limited_availability", "shortage", "discontinuation")


def in_shortage(p: dict) -> bool:
    """On an EOF limited-availability, shortage or discontinuation list. EMA entries
    do not count: they do not say whether they concern Greece (Δ-21)."""
    return any(a["type"] in EOF_LISTS for a in p.get("availability") or [])


@dataclass
class Equivalents:
    """Packages that compare with one package under Δ-16, that package included,
    cheapest first (per unit, or per pack when all have the same size)."""

    rows: list[dict]
    per_unit: bool
    counts: dict[str, int | None]

    def key(self, q: dict) -> float:
        return q["prices"]["retail"] / self.counts[q["eof_code"]] if self.per_unit else q["prices"]["retail"]

    def available(self, p: dict) -> list[dict]:
        """The others that are on no EOF shortage or discontinuation list, cheapest first."""
        return [q for q in self.rows if q is not p and not in_shortage(q)]


def comparable(ctx: Context, p: dict) -> Equivalents | None:
    if p["eof_code"] in ctx.compared:
        return ctx.compared[p["eof_code"]]
    eq = None
    codes = ctx.equiv.get(p["eof_code"], [])
    if len(codes) >= 2:
        rows = [ctx.by_code[c] for c in codes]
        counts = {q["eof_code"]: unit_count(q["name"]) for q in rows}
        # Compare like with like: per unit when the number of tablets/capsules is
        # known; otherwise only boxes of the same size. Never box against box of
        # different sizes (a 28-pack is not "62% cheaper" than a 56-pack).
        per_unit = counts[p["eof_code"]] is not None
        if per_unit:
            rows = [q for q in rows if counts[q["eof_code"]]]
        else:
            n = pack_count(p["name"])
            rows = [q for q in rows if n is not None and pack_count(q["name"]) == n]
        if len(rows) >= 2:
            eq = Equivalents(rows, per_unit, counts)
            rows.sort(key=lambda q: (eq.key(q), q["prices"]["retail"], q["name"]))
    ctx.compared[p["eof_code"]] = eq
    return eq


def _gap_note(p: dict, q: dict) -> str:
    """What the reimbursement leaves to the patient for one pack of `q`, when `p` is reimbursed."""
    if not p["reimbursement"]:
        return ""
    if not q["reimbursement"]:
        return '<span class="rd">χωρίς αποζημίωση</span>'
    gap = max(q["prices"]["retail"] - q["reimbursement"][0]["reimbursement_price"], 0)
    return f'<span class="rd">{"διαφορά " + eur(gap) if gap > 0.004 else "χωρίς διαφορά"}</span>'


def equivalents_card(ctx: Context, p: dict) -> str:
    eq = comparable(ctx, p)
    if not eq:
        return ""
    st = strength(p["name"])
    rows, key, per_unit = eq.rows, eq.key, eq.per_unit
    mine = key(p)
    cheaper = [q for q in rows if key(q) < mine * 0.995]
    # A package in shortage: first the equivalents that are on no EOF list, so
    # the reader sees what can be found; then the rest, in the same price order.
    short = bool(p.get("availability"))
    avail = eq.available(p) if short else []
    ordered = (avail + [q for q in rows if q is p or in_shortage(q)]) if short else rows
    shown = ordered[:7]
    if not any(q is p for q in shown):
        shown.append(p)
    gaps = bool(short and p["reimbursement"] and any(q["reimbursement"] for q in shown if q is not p))
    lis = []
    for q in shown:
        n = eq.counts.get(q["eof_code"]) or pack_count(q["name"])
        extra = " · ".join(x for x in ((f"×{n}" if n else ""), short_mah(q["mah"])) if x)
        label = f"<b>{e(split_name(q['name'])[0])}</b> <span>· {e(extra)}</span>{avail_tag(q)}"
        gap = _gap_note(p, q) if gaps else ""
        u = f'<span class="u">{eur(key(q))}/τεμ.</span>' if per_unit else ""
        price = f'<span class="p">{eur(q["prices"]["retail"])}</span>'
        if q is p:
            lis.append(
                f'<li class="me"><span class="nm">{label}</span>{gap}{u}{price}'
                '<span class="pill here">αυτό</span></li>'
            )
            continue
        k = key(q)
        rel = (
            f'<span class="pill {"down" if k < mine else "up"}">{pct(k, mine)}</span>'
            if abs(k - mine) / mine > 0.005
            else '<span class="pill same">ίδια</span>'
        )
        cls = ' class="short"' if short and in_shortage(q) else ""
        lis.append(
            f'<li{cls}><a class="nm" href="/farmako/{e(q["eof_code"])}/">{label}</a>{gap}{u}{price}{rel}</li>'
        )
    basis = "ανά τεμάχιο" if per_unit else "για ίδιο μέγεθος συσκευασίας"
    if short and avail:
        first = "εμφανίζεται πρώτο" if len(avail) == 1 else "εμφανίζονται πρώτα, από το φθηνότερο"
        word = "ισοδύναμο δεν είναι" if len(avail) == 1 else "ισοδύναμα δεν είναι"
        summary = f"{len(avail)} {word} σε λίστα ελλείψεων ή διακοπής του ΕΟΦ· {first}"
        summary += " ανά τεμάχιο." if per_unit and len(avail) > 1 else "."
    elif short:
        summary = "Όλα τα ισοδύναμα είναι κι αυτά σε λίστα ελλείψεων ή διακοπής του ΕΟΦ."
    elif cheaper:
        best = (mine - key(cheaper[0])) / mine * 100
        word = "φθηνότερη" if len(cheaper) == 1 else "φθηνότερες"
        summary = f"{len(cheaper)} {word} {basis}, έως <b>{num(best, 0)}%</b> λιγότερο."
    else:
        summary = f"Η φθηνότερη επιλογή {basis}."
    notes = ""
    if short:
        notes = (
            '<p class="small">Ενδείξεις έλλειψης από τις λίστες του ΕΟΦ (<a href="/elleipseis/">ελλείψεις</a>)· '
            "η διαθεσιμότητα επιβεβαιώνεται στο φαρμακείο."
        )
        if gaps:
            rs = p["reimbursement"][0]["source"]
            notes += (
                " Διαφορά: όσο η λιανική ξεπερνά την τιμή αποζημίωσης, ανά συσκευασία "
                f"(κατάλογος αποζημίωσης {date_gr(rs['published'])})."
            )
        notes += "</p>"
    subs = " + ".join(sname(ctx, s) for s in p["substances"])
    more = (
        f'<a href="/ousia/{e(ctx.slug_of[p["substances"][0]])}/">Όλες οι {len(rows)}</a>'
        if len(rows) > len(shown)
        else ""
    )
    return f"""<section class="card list-card" id="isodynama">
  <div class="section-head"><h2>{e(subs)} {e(st[1] if st else "")}</h2>{more}</div>
  <p class="small top">{summary}</p>
  <ul class="rows eq">{"".join(lis)}</ul>
  {notes}
  <p class="small">Ίδια δραστική ουσία, περιεκτικότητα και μορφή. Αλλαγή σκευάσματος μόνο με τη γνώμη γιατρού ή φαρμακοποιού.</p>
</section>"""


def docs_card(ctx: Context, p: dict) -> str:
    """Official texts. EMA links for centrally authorised medicines; for the rest
    the EOF search (a link the visitor follows, not something we crawl)."""
    pis = [x for x in p.get("product_information", []) if x.get("smpc_pil_el_url") or x.get("epar_url")]
    if pis:
        x = pis[0]
        lf = ctx.leaflets.get(x.get("product_number"))
        if lf:
            read = []
            if lf["pil"]:
                read.append(
                    f'<a class="doc" href="/fylla/{e(lf["slug"])}/">Φύλλο οδηγιών χρήσης</a><div>Για τον ασθενή · σε ενότητες</div>'
                )
            if lf.get("quick"):
                read.append(
                    '<div class="quick small-quick">'
                    + "".join(
                        f'<a class="chip" href="/fylla/{e(lf["slug"])}/#{e(frag)}">{e(label)}</a>'
                        for label, frag in lf["quick"]
                    )
                    + "</div>"
                )
            if lf["smpc"]:
                href = f"/fylla/{e(lf['slug'])}/pxp/" if lf["pil"] else f"/fylla/{e(lf['slug'])}/"
                read.append(
                    f'<a class="doc" href="{href}">Περίληψη χαρακτηριστικών (ΠΧΠ)</a><div>Για επαγγελματίες · σε ενότητες</div>'
                )
            pdf = (
                f' · <a href="{e(x["smpc_pil_el_url"])}" rel="noopener">pdf ↗</a>'
                if x.get("smpc_pil_el_url")
                else ""
            )
            return f"""<section class="card side">
      <h2>Επίσημα κείμενα</h2>
      <div class="docs">{"".join(read)}<p class="small">Πηγή: EMA{pdf}</p></div>
    </section>"""
        main = (
            f'<a class="doc" href="{e(x["smpc_pil_el_url"])}" rel="noopener">ΠΧΠ &amp; Φύλλο οδηγιών ↗</a>'
            "<div>Ελληνικά · pdf · European Medicines Agency</div>"
            if x.get("smpc_pil_el_url")
            else ""
        )
        epar = f'<a href="{e(x["epar_url"])}" rel="noopener">Σελίδα EPAR ↗</a>' if x.get("epar_url") else ""
        return f"""<section class="card side">
      <h2>Επίσημα κείμενα</h2>
      <div class="docs">{main}<p class="small">{epar} · {e(x["product_number"])}</p></div>
    </section>"""
    docs = {d["type"]: d for d in p.get("documents", [])}
    if docs:
        rows = []
        for t, label in (("pil", "Φύλλο οδηγιών χρήσης"), ("smpc", "Περίληψη χαρακτηριστικών (ΠΧΠ)")):
            d = docs.get(t)
            if d:
                date = f" · {date_gr(d['date'])}" if d.get("date") else ""
                rows.append(
                    f'<a class="doc" href="{e(d["url"])}" rel="noopener">{label} ↗</a><div>ΕΟΦ{date}</div>'
                )
        return f"""<section class="card side">
      <h2>Επίσημα κείμενα</h2>
      <div class="docs">{"".join(rows)}<p class="small">Πηγή: Εθνικός Οργανισμός Φαρμάκων</p></div>
    </section>"""
    return """<section class="card side">
      <h2>Επίσημα κείμενα</h2>
      <p class="small first">ΠΧΠ και φύλλο οδηγιών: <a href="https://services.eof.gr/human-search/" rel="noopener">αναζήτηση στον ΕΟΦ ↗</a></p>
    </section>"""


AVAIL = {
    "limited_availability": ("Περιορισμένη διαθεσιμότητα", "warn", "Λίστα επάρκειας ΕΟΦ"),
    "shortage": ("Έλλειψη", "warn", "Ανακοίνωση ΕΟΦ"),
    "discontinuation": ("Οριστική διακοπή κυκλοφορίας", "stop", "Λίστα διακοπών ΕΟΦ"),
    "eu_shortage": ("Έλλειψη σε επίπεδο ΕΕ", "eu", "EMA"),
}
EU_REASON = {
    "Shortage ongoing": "σε εξέλιξη",
    "Medicine discontinued": "διακοπή",
    "Shortage resolved": "επιλύθηκε",
}
AVAIL_ORDER = ("discontinuation", "limited_availability", "shortage", "eu_shortage")


def availability_card(ctx: Context, p: dict) -> str:
    items = p.get("availability") or []
    if not items:
        return ""
    rows = []
    for a in sorted(items, key=lambda a: AVAIL_ORDER.index(a["type"]) if a["type"] in AVAIL_ORDER else 9):
        title, cls, src = AVAIL.get(a["type"], (a["type"], "warn", ""))
        bits = []
        if a.get("reason"):
            bits.append(EU_REASON.get(a["reason"], a["reason"]))
        if a.get("start"):
            future = a["start"] > ctx.meta["built_at"][:10]
            bits.append(("αναμένεται από " if future else "από ") + date_gr(a["start"]))
        if a.get("valid_until"):
            bits.append("έως " + date_gr(a["valid_until"]))
        detail = f"<span>{e(' · '.join(bits))}</span>" if bits else ""
        alt = f'<p class="alt">Πρόταση ΕΟΦ: {e(a["alternatives"])}</p>' if a.get("alternatives") else ""
        when = f" · {date_gr(a['published'])}" if a.get("published") else ""
        link = (
            f'<a href="{e(a["source_url"])}" rel="noopener">{e(src)}{when} ↗</a>'
            if a.get("source_url")
            else e(src)
        )
        rows.append(
            f'<div class="av {cls}"><strong>{e(title)}</strong>{detail}{alt}<p class="avsrc">{link}</p></div>'
        )
    eq = comparable(ctx, p)
    if not eq:
        s = p["substances"][0] if p["substances"] else None
        link = (
            f'· <a href="/ousia/{e(ctx.slug_of[s])}/">όλες οι συσκευασίες με {e(sname(ctx, s))}</a>'
            if s in ctx.slug_of
            else "."
        )
        more = f'<p class="av-more">Δεν βρέθηκε ισοδύναμο με ίδια ουσία, περιεκτικότητα και μορφή{link}</p>'
    elif n := len(eq.available(p)):
        word = "ισοδύναμο" if n == 1 else "ισοδύναμα"
        more = f'<p class="av-more"><a href="#isodynama">{n} {word} χωρίς ένδειξη έλλειψης ↓</a></p>'
    else:
        more = (
            '<p class="av-more">Κανένα ισοδύναμο χωρίς ένδειξη έλλειψης· '
            '<a href="#isodynama">όλα τα ισοδύναμα ↓</a></p>'
        )
    note = '<p class="small first">Επίσημες λίστες, όχι το απόθεμα των φαρμακείων.</p>'
    return f'<section class="card avail" aria-label="Διαθεσιμότητα">{"".join(rows)}{more}{note}</section>'


def available_equivalents(ctx: Context, p: dict) -> str:
    """For the shortages list: how many equivalents are on no EOF list, linking to them."""
    eq = comparable(ctx, p)
    if not eq:
        return '<span class="eqn none">κανένα ισοδύναμο</span>'
    n = len(eq.available(p))
    text = (
        f"{n} {'ισοδύναμο' if n == 1 else 'ισοδύναμα'} χωρίς έλλειψη"
        if n
        else "κανένα ισοδύναμο χωρίς έλλειψη"
    )
    cls = "eqn" if n else "eqn none"
    return f'<a class="{cls}" href="/farmako/{e(p["eof_code"])}/#isodynama">{text}</a>'


def avail_tag(p: dict) -> str:
    types = {a["type"] for a in p.get("availability") or []}
    if "discontinuation" in types:
        return ' <span class="tag stop" title="Οριστική διακοπή κυκλοφορίας (ΕΟΦ)">Διακοπή</span>'
    if types & {"limited_availability", "shortage"}:
        return ' <span class="tag warn" title="Περιορισμένη διαθεσιμότητα (ΕΟΦ)">Έλλειψη</span>'
    return ""


def price_card(p: dict, vat: str, was: str) -> str:
    pr = p["prices"]
    if p.get("kind") == "otc":
        return f"""<section class="card price">
      <div><div class="label">Ενδεικτική τιμή λιανικής</div><div class="big">{eur(pr["retail"])}</div>
      <p class="was">Μη συνταγογραφούμενο (ΜΗΣΥΦΑ): τιμή που συνιστά το Υπουργείο· το φαρμακείο ορίζει τη δική του.</p></div>
      <dl class="kv"><dt>ΦΠΑ</dt><dd>{vat}</dd></dl>
    </section>"""
    return f"""<section class="card price">
      <div><div class="label">Λιανική τιμή</div><div class="big">{eur(pr["retail"])}</div>{was}</div>
      <dl class="kv">
        <dt>Χονδρική</dt><dd>{eur(pr["wholesale"])}</dd>
        <dt>Παραγωγού</dt><dd>{eur(pr["exfactory"])}</dd>
        <dt>ΦΠΑ</dt><dd>{vat}</dd>
      </dl>
    </section>"""


def package(ctx: Context, p: dict) -> str:
    pr = p["prices"]
    otc = p.get("kind") == "otc"
    brand, rest = split_name(p["name"])
    subs = p["substances"]
    crumbs = (
        f'<a href="/ousia/{e(ctx.slug_of[subs[0]])}/">{e(sname(ctx, subs[0]))}</a>' if subs else "Συσκευασία"
    )
    chips = [
        f'<a class="chip sub-link" href="/ousia/{e(ctx.slug_of[s])}/" title="{e(inn_label(s))}">{e(sname(ctx, s))}</a>'
        for s in subs
    ]
    fam, form_label = form_kind(p["name"])
    if fam != "other":
        chips.append(f'<span class="chip">{e(form_label)}</span>')
    pis = [pi.get("product_number") for pi in p.get("product_information") or []][:1]
    for key, who in [(k, "EMA") for k in pis] or [(f"EOF-{p['eof_code'][:7]}", "ΕΟΦ")]:
        for flag in (ctx.leaflets.get(key) or {}).get("flags", []):
            chips.append(f'<span class="chip flag" title="Από το επίσημο κείμενο ({who})">{e(flag)}</span>')
    if p["mah"]:
        chips.append(f'<span class="chip" title="{e(p["mah"])}">{e(short_mah(p["mah"]))}</span>')
    disp = dispensing_label(p["reimbursement"][0]["dispensing"]) if p["reimbursement"] else None
    if disp:
        chips.append(f'<span class="chip">{e(disp)}</span>')
    if otc:
        chips.append('<span class="chip otc">Χωρίς συνταγή (ΜΗΣΥΦΑ)</span>')
    elif p["non_reimbursed"]:
        chips.append('<span class="chip warn">Μη αποζημιούμενο</span>')
    vat = f"{num(p['vat'] * 100, 0)}%" if p["vat"] else "—"
    was = ""
    upd = [
        h for h in p["price_history"] if h["field"] == "price_retail" and h["change"] == "update" and h["old"]
    ]
    if upd:
        last = max(upd, key=lambda h: h["published"])
        was = f'<p class="was">Ήταν {eur(last["old"])} έως {date_gr(last["published"])}</p>'
    ps = p["price_source"]
    srcs = [
        f'<li><a href="{e(ps["url"])}" rel="noopener">{"Δελτίο τιμών ΜΗΣΥΦΑ" if otc else "Δελτίο τιμών"} ↗</a><div>Υπουργείο Υγείας · {date_gr(ps["published"])}</div></li>'
    ]
    if p["reimbursement"]:
        rs = p["reimbursement"][0]["source"]
        srcs.append(
            f'<li><a href="{e(rs["url"])}" rel="noopener">Κατάλογος αποζημίωσης ↗</a><div>Υπουργείο Υγείας · {date_gr(rs["published"])}</div></li>'
        )
    # the package leaflet on the page itself: answers first, the whole text below (Δ-23)
    # EMA text for centrally authorised medicines, else the EOF one (Δ-24)
    glance, leaflet = "", ""
    lf = next(
        (
            ctx.leaflets[pi["product_number"]]
            for pi in p.get("product_information") or []
            if ((ctx.leaflets.get(pi.get("product_number")) or {}).get("texts") or {}).get("pil")
        ),
        None,
    ) or ctx.leaflets.get(f"EOF-{p['eof_code'][:7]}")
    if lf:
        from . import leaflets  # imports this module

        glance, leaflet = leaflets.package_parts(lf, p["name"], ctx.glance_failed)
    if leaflet:
        texts = lf["texts"]
        who = leaflets.source_short(texts)
        for kind, label in (("pil", "Φύλλο οδηγιών"), ("smpc", "ΠΧΠ")):
            src = (texts.get("sources") or {}).get(kind) or (texts.get("source") if kind == "pil" else None)
            if not src or not src.get("url"):
                continue
            if who == "EMA":
                label = "Φύλλο οδηγιών &amp; ΠΧΠ"
            when = leaflets.source_date({"source": src}) or leaflets.source_date(texts)
            srcs.append(
                f'<li><a href="{e(src["url"])}" rel="noopener">{label} ↗</a>'
                f"<div>{e(who)}{f' · {when}' if when else ''}</div></li>"
            )
            if who == "EMA":
                break
        rows = []
        if texts.get("pil"):
            rows.append(
                '<a class="doc" href="#fyllo">Φύλλο οδηγιών χρήσης ↓</a><div>Σε αυτή τη σελίδα · σε ενότητες</div>'
            )
        if texts.get("smpc") and lf.get("slug"):
            rows.append(
                f'<a class="doc" href="/fylla/{e(lf["slug"])}/pxp/">Περίληψη χαρακτηριστικών (ΠΧΠ)</a><div>Για επαγγελματίες · σε ενότητες</div>'
            )
        elif texts.get("smpc"):
            rows.append(
                '<a class="doc" href="#pxp">Περίληψη χαρακτηριστικών (ΠΧΠ) ↓</a><div>Για επαγγελματίες · σε αυτή τη σελίδα</div>'
            )
        docs = f"""<section class="card side">
      <h2>Επίσημα κείμενα</h2>
      <div class="docs">{"".join(rows)}<p class="small">Πηγή: {e(who)}</p></div>
    </section>"""
    else:
        docs = docs_card(ctx, p)
    issue = quote(f"Λάθος στη συσκευασία {p['eof_code']}")
    body = f"""
<div class="wrap">
<nav class="crumbs" aria-label="Διαδρομή"><a href="/">Αρχική</a> / {crumbs}</nav>
<div class="title">
  <h1>{e(brand)}</h1>
  <p class="form">{e(rest)}</p>
  <div class="chips">{"".join(chips)}</div>
</div>
<div class="cols">
  <div class="main">
    {availability_card(ctx, p)}
    {glance}
    {price_card(p, vat, was)}
    {"" if otc else reimbursement_card(ctx, p)}
    {equivalents_card(ctx, p)}
    {"" if otc else history_card(ctx, p)}
  </div>
  <aside>
    <section class="card side">
      <dl class="codes">
        <div><dt>Κωδικός ΕΟΦ</dt><dd>{e(p["eof_code"])}</dd></div>
        <div><dt>Barcode</dt><dd>{e(p["barcode"])}</dd></div>
        <div><dt>ATC</dt><dd>{e(p["atc"] or "—")}</dd></div>
        <div><dt>Κάτοχος άδειας</dt><dd class="txt">{e(p["mah"] or "—")}</dd></div>
      </dl>
    </section>
    {docs}
    <section class="card side">
      <h2>Πηγές</h2>
      <ul class="srcs">{"".join(srcs)}</ul>
      <p class="small"><a href="/sxetika/#ti-deixnei">Η τιμή δεν σημαίνει ότι κυκλοφορεί</a></p>
    </section>
    <div class="links"><a href="/api/v1/packages/{e(p["eof_code"])}">JSON</a><a href="{REPO}/issues/new?title={issue}">Αναφορά λάθους</a></div>
  </aside>
</div>
{leaflet}
</div>
"""
    subst = ", ".join(sname(ctx, s) for s in subs) or "—"
    return layout(
        ctx,
        f"{brand} {rest} · {eur(pr['retail'])} · farmaka.info",
        body,
        f"/farmako/{p['eof_code']}/",
        f"{p['name']} ({subst}). Λιανική {eur(pr['retail'])}, αποζημίωση, φθηνότερα ισοδύναμα και ιστορικό τιμής, "
        "από το Υπουργείο Υγείας." + (" Φύλλο οδηγιών χρήσης σε ενότητες." if leaflet else ""),
    )


# ---------------------------------------------------------------- substance


def _row(p: dict) -> str:
    fam, label = form_kind(p["name"])
    tag = ' <span class="tag" title="Στον κατάλογο αποζημίωσης">Α</span>' if p["reimbursement"] else ""
    if p.get("kind") == "otc":
        tag = ' <span class="tag otc" title="Μη συνταγογραφούμενο, ενδεικτική τιμή">ΜΗΣΥΦΑ</span>'
    tag += avail_tag(p)
    return (
        f'<li data-form="{fam}" data-price="{p["prices"]["retail"]:.2f}" data-name="{e(p["name"])}">'
        f'<a class="nm" href="/farmako/{e(p["eof_code"])}/">{name_html(p["name"])}</a>{tag}'
        f'<span class="kind">{e(label)}</span>'
        f'<span class="mah">{e(short_mah(p["mah"]))}</span><span class="p">{eur(p["prices"]["retail"])}</span></li>'
    )


def _filters(rows: list[dict]) -> str:
    """Form filter and sort controls; hidden until app.js wires them (no-JS shows the full list)."""
    fams: dict[str, int] = {}
    for p in rows:
        f = form_kind(p["name"])[0]
        fams[f] = fams.get(f, 0) + 1
    if len(fams) < 2 and len(rows) < 6:
        return ""
    chips = [
        f'<button type="button" class="fchip" data-form="all" aria-pressed="true">Όλες <span>{len(rows)}</span></button>'
    ]
    for f in FORM_FAMILIES:
        if f in fams:
            chips.append(
                f'<button type="button" class="fchip" data-form="{f}" aria-pressed="false">{e(FORM_FAMILIES[f])} <span>{fams[f]}</span></button>'
            )
    chips_html = "".join(chips) if len(fams) > 1 else ""
    return f"""<div class="filters" hidden>
  <div class="fchips" role="group" aria-label="Μορφή">{chips_html}</div>
  <label class="sort">Ταξινόμηση <select data-sort><option value="price">Τιμή</option><option value="name">Όνομα</option></select></label>
</div>"""


def _strength_sort(label: str) -> tuple:
    head = label.split()[0].replace(",", ".") if label else ""
    try:
        return (0, float(head.split("+")[0]), label)
    except ValueError:
        return (1, 0.0, label)


def substance(ctx: Context, s: dict) -> str:
    rows = [ctx.by_code[c] for c in s["codes"]]
    single = [p for p in rows if p["substances"] == [s["name"]]]
    combo = [p for p in rows if p["substances"] != [s["name"]]]
    parts = []
    groups: dict[str, list] = {}
    for p in single:
        st = strength(p["name"])
        groups.setdefault(st[1] if st else "Άλλες", []).append(p)
    for label, ps in sorted(groups.items(), key=lambda kv: _strength_sort(kv[0])):
        ps.sort(key=lambda p: (p["prices"]["retail"], p["name"]))
        parts.append(
            f'<section class="group"><h2>{e(label)} <span>{len(ps)}</span></h2>'
            f'<ul class="rows">{"".join(_row(p) for p in ps)}</ul></section>'
        )
    cg: dict[tuple, list] = {}
    for p in combo:
        cg.setdefault(tuple(p["substances"]), []).append(p)
    for names, ps in sorted(cg.items()):
        ps.sort(key=lambda p: (p["prices"]["retail"], p["name"]))
        title = " + ".join(sname(ctx, n) for n in names)
        parts.append(
            f'<section class="group"><h2>{e(title)} <span>{len(ps)}</span></h2>'
            f'<ul class="rows">{"".join(_row(p) for p in ps)}</ul></section>'
        )
    atcs = sorted({p["atc"] for p in rows if p["atc"]})
    reimb = sum(1 for p in rows if p["reimbursement"])
    body = f"""
<div class="wrap">
<nav class="crumbs" aria-label="Διαδρομή"><a href="/">Αρχική</a> / Δραστική ουσία</nav>
<div class="page-head">
  <h1>{e(sname(ctx, s["name"]))}</h1>
  {f'<p class="inn">{e(inn_label(s["name"]))}</p>' if s.get("name_el") else ""}
  <p>{num(len(rows), 0)} συσκευασίες · {num(reimb, 0)} αποζημιώνονται · ATC {e(", ".join(atcs[:4]) or "—")}</p>
</div>
{_filters(rows)}
{"".join(parts)}
</div>
"""
    return layout(
        ctx,
        f"{sname(ctx, s['name'])} ({inn_label(s['name'])}) · συσκευασίες και τιμές · farmaka.info"
        if s.get("name_el")
        else f"{inn_label(s['name'])} · συσκευασίες και τιμές · farmaka.info",
        body,
        f"/ousia/{s['slug']}/",
        f"Όλες οι συσκευασίες με {sname(ctx, s['name'])} ({inn_label(s['name'])}) στην Ελλάδα, με τιμή και αποζημίωση.",
    )


# ---------------------------------------------------------------- data & about


def _size(n: int) -> str:
    if n >= 1_000_000:
        return f"{num(n / 1_000_000, 1)} MB"
    return f"{num(n / 1000, 0)} kB"


DOWNLOAD_INFO = {
    "packages.csv": "Μία γραμμή ανά συσκευασία, με τιμές και πηγή",
    "price_history.csv": "Κάθε αλλαγή τιμής, με το δελτίο της",
    "reimbursement.csv": "Τιμή αποζημίωσης, DDD, κόστος ημέρας, περιορισμοί",
    "farmaka.json": "Όλα μαζί, μία εγγραφή ανά συσκευασία",
    "farmaka.sqlite": "Βάση SQLite (package, price_event, reimbursement, source)",
    "sources.json": "Τα δελτία που χρησιμοποιήθηκαν",
    "build_report.json": "Έλεγχοι, ουρά ελέγχου, δελτία σε αναμονή",
    "ema_links.csv": "Σύνδεσμοι σε ΠΧΠ/ΦΟΧ της EMA (πηγή: EMA)",
    "otc_packages.csv": "Μη συνταγογραφούμενα (ΜΗΣΥΦΑ) με ενδεικτική τιμή λιανικής",
    "shortages.csv": "Ελλείψεις και διακοπές από τις λίστες του ΕΟΦ και την EMA",
    "allages.csv": f"Οι αλλαγές της σελίδας «Τι άλλαξε» ({changes.WINDOW_DAYS} ημέρες), με το δελτίο ή τη λίστα τους",
}

PACKAGE_FIELDS = [
    ("eof_code", "Κωδικός ΕΟΦ, 9 ψηφία· μοναδικό κλειδί"),
    ("barcode", "EAN-13 με πρόθεμα 280· περιέχει τον κωδικό ΕΟΦ"),
    ("name", "Ονομασία όπως στο δελτίο"),
    ("atc", "Κωδικός ATC"),
    ("substances", "Δραστικές ουσίες, χωρισμένες με |"),
    ("mah", "Κάτοχος άδειας κυκλοφορίας"),
    ("non_reimbursed", "1 αν το δελτίο το σημειώνει μη αποζημιούμενο"),
    ("price_exfactory / price_wholesale / price_retail", "Τιμή παραγωγού, χονδρική, λιανική (€)"),
    ("vat", "ΦΠΑ ως κλάσμα (0.06 = 6%)"),
    ("price_published / price_moh_article", "Δελτίο που όρισε την τρέχουσα τιμή"),
]


def data_page(ctx: Context) -> str:
    m = ctx.meta
    dls = "".join(
        f'<tr><td><a href="/data/{e(d["name"])}">{e(d["name"])}</a></td><td>{e(DOWNLOAD_INFO.get(d["name"], ""))}</td>'
        f'<td class="r">{_size(d["bytes"])}</td></tr>'
        for d in ctx.downloads
        if d["name"] != "SHA256SUMS"
    )
    srcs = "".join(
        f'<li>{date_gr(s["published"])} · <a href="{e(s.get("url", ""))}" rel="noopener">{e(s.get("title") or s["file"])}</a></li>'
        for s in m["sources"]
    )
    if m.get("reimbursement_source"):
        rs = m["reimbursement_source"]
        srcs += f'<li>{date_gr(rs["published"])} · <a href="{e(rs["url"])}" rel="noopener">{e(rs["title"])}</a></li>'
    for o in (m.get("otc_source") or {}).get("sources", []):
        srcs += f'<li>{date_gr(o["published"])} · <a href="{e(o["url"])}" rel="noopener">{e(o["title"])}</a> (ΜΗΣΥΦΑ)</li>'
    pis = m.get("product_information_source")
    if pis:
        srcs += (
            f"<li>{e((pis.get('generated') or '')[:10])} · European Medicines Agency: ελληνικά ΠΧΠ/ΦΟΧ για "
            f"{num(pis['packages_linked'], 0)} συσκευασίες κεντρικά εγκεκριμένων φαρμάκων "
            f'(<a href="{e(pis["terms"])}" rel="noopener">όροι: αναφορά της EMA ως πηγής</a>)</li>'
        )
    pend = (
        "<p>Σε αναμονή:</p><ul>"
        + "".join(
            f'<li>{date_gr(x["published"])} · <a href="{e(x["url"])}" rel="noopener">{e(x["title"])}</a>'
            f' <span class="muted">({"δεν έχει ληφθεί" if x["reason"] == "not downloaded yet" else "χειροκίνητος έλεγχος"})</span></li>'
            for x in m["pending_bulletins"]
        )
        + "</ul>"
        if m["pending_bulletins"]
        else ""
    )
    fields = "".join(f"<tr><td><code>{e(k)}</code></td><td>{e(v)}</td></tr>" for k, v in PACKAGE_FIELDS)
    ex = "234280202" if "234280202" in ctx.by_code else next(iter(ctx.by_code))
    body = f"""
<div class="wrap narrow prose">
<h1>Δεδομένα &amp; API</h1>
<p class="lead">Ελεύθερα για κάθε χρήση (CC0). Χωρίς εγγραφή, χωρίς κλειδί.</p>

<h2 id="lipseis">Λήψεις</h2>
<div class="tw"><table class="table"><thead><tr><th>Αρχείο</th><th>Περιεχόμενο</th><th class="r">Μέγεθος</th></tr></thead><tbody>{dls}</tbody></table></div>
<p class="small">UTF-8, δεκαδικά με τελεία. Έλεγχος: <a href="/data/SHA256SUMS">SHA256SUMS</a>.</p>
<p class="small">Ροή <a href="/allages/rss.xml">RSS</a> με τις αλλαγές της σελίδας <a href="/allages/">Τι άλλαξε</a>, μία εγγραφή ανά ημερομηνία δελτίου ή λίστας.</p>

<h2 id="api">API</h2>
<div class="tw"><table class="table"><tbody>
<tr><td><a href="/api/v1/packages/{e(ex)}"><code>/api/v1/packages/{{κωδικός ΕΟΦ}}</code></a></td><td>Μία συσκευασία</td></tr>
<tr><td><a href="/api/v1/barcode/{e(ctx.by_code[ex]["barcode"])}"><code>/api/v1/barcode/{{EAN-13}}</code></a></td><td>Με barcode</td></tr>
<tr><td><a href="/api/v1/search?q=lipitor"><code>/api/v1/search?q=…</code></a></td><td>Αναζήτηση</td></tr>
<tr><td><a href="/api/v1/substances"><code>/api/v1/substances</code></a></td><td>Δραστικές ουσίες</td></tr>
<tr><td><a href="/api/v1/meta"><code>/api/v1/meta</code></a></td><td>Ενημέρωση και πηγές</td></tr>
</tbody></table></div>
<p class="small">JSON, CORS ανοιχτό. Για μαζική χρήση προτιμήστε τις λήψεις.</p>

<h2 id="katastasi">Πηγές και ενημέρωση</h2>
<ul>{srcs}</ul>
{pend}

<details><summary>Πεδία του packages.csv</summary>
<div class="tw"><table class="table"><tbody>{fields}</tbody></table></div>
<p class="small">Σταθερά ονόματα· αλλαγή σημαίνει νέο <code>schema_version</code> (τώρα {e(m["schema_version"])}).</p>
</details>

<details><summary>Γνωστά κενά</summary>
<ul>
<li>Ιστορικό τιμών από {date_gr(m["sources"][0]["published"])}· τα παλαιότερα δελτία προστίθενται.</li>
<li>Ημερομηνία τιμής = δημοσίευση δελτίου· η ισχύς μπορεί να διαφέρει.</li>
<li>Φάρμακα εκτός δελτίου τιμών (π.χ. πολλά μη συνταγογραφούμενα) δεν εμφανίζονται.</li>
<li>Δεν ξέρουμε ποιες συσκευασίες κυκλοφορούν: η βάση έχει όσες έχουν τιμή.</li>
<li>Τροποποιήσεις αποφάσεων ελέγχονται χειροκίνητα.</li>
<li>ΠΧΠ και φύλλα οδηγιών: εκκρεμεί επίσημο αίτημα προς τον ΕΟΦ.</li>
</ul>
</details>

<h2 id="adeia">Άδεια</h2>
<p>Δεδομένα <a href="https://creativecommons.org/publicdomain/zero/1.0/deed.el" rel="noopener">CC0 1.0</a>. Κώδικας <a href="{REPO}/blob/main/LICENSE">AGPL-3.0</a>.</p>
</div>
"""
    return layout(
        ctx,
        "Δεδομένα & API · farmaka.info",
        body,
        "/dedomena/",
        "Ανοιχτά δεδομένα φαρμάκων (CC0) και API χωρίς κλειδί.",
        nav="data",
    )


def about(ctx: Context) -> str:
    body = f"""
<div class="wrap narrow prose">
<h1>Σχετικά</h1>
<p class="lead">Ελεύθερη, ανοιχτή βάση πληροφοριών για τα φάρμακα στην Ελλάδα.</p>
<p>Τα δελτία τιμών και ο κατάλογος αποζημίωσης του Υπουργείου Υγείας είναι δημόσια, αλλά σκόρπια σε
εκατοντάδες αρχεία. Εδώ ενώνονται ανά συσκευασία, με ιστορικό και σύνδεσμο στην πηγή κάθε στοιχείου.</p>

<h2>Τι δεν είναι</h2>
<p>Ιατρική συμβουλή. Για την υγεία σας ρωτήστε γιατρό ή φαρμακοποιό. Αν κάτι διαφέρει από την επίσημη πηγή, ισχύει η πηγή.</p>

<h2 id="ti-deixnei">Τι δείχνουν τα στοιχεία</h2>
<ul>
<li><b>Ποια φάρμακα.</b> Κάθε συσκευασία για την οποία το Υπουργείο Υγείας έχει ορίσει τιμή. Αυτό δεν σημαίνει ότι
κυκλοφορεί: ένα φάρμακο μπορεί να έχει τιμή χωρίς να έχει βγει στην αγορά ή αφού έχει σταματήσει. Αν υπάρχει, το
ξέρει το φαρμακείο.</li>
<li><b>Τιμές μη συνταγογραφούμενων (ΜΗΣΥΦΑ).</b> Η ενδεικτική τιμή του Υπουργείου: συνιστώμενη, όχι υποχρεωτική.
Το φαρμακείο ορίζει τη δική του.</li>
<li><b>Ελλείψεις.</b> Οι επίσημες λίστες του ΕΟΦ και της EMA, όχι τα ράφια των φαρμακείων. Ένα φάρμακο της λίστας
μπορεί να βρίσκεται, κι ένα που λείπει μπορεί να μην είναι σε καμία λίστα.</li>
<li><b>«Πόσο θα πληρώσω;»</b> Ενδεικτικός υπολογισμός από την τιμή αποζημίωσης. Το ποσοστό το ορίζει η συνταγή και
υπάρχουν ειδικοί κανόνες που δεν περιλαμβάνονται· το τελικό ποσό το λέει ο φαρμακοποιός.</li>
</ul>
<p>Οι επίσημες λίστες συχνά καθυστερούν. Ο γιατρός και ο φαρμακοποιός βλέπουν τη συνταγή και το απόθεμα· όταν
λένε κάτι άλλο από αυτό το site, μάλλον έχουν δίκιο. Αν βρείτε λάθος, <a href="{REPO}/issues">πείτε μας</a>.</p>

<h2 id="egkatastasi">Εγκατάσταση στο κινητό</h2>
<p>Το site εγκαθίσταται σαν εφαρμογή και ανοίγει και χωρίς σύνδεση για όσα έχετε ήδη δει.
Android/Chrome: «Εγκατάσταση εφαρμογής» από το μενού ή το κουμπί στο κάτω μέρος της σελίδας.
iPhone/Safari: Κοινοποίηση → «Προσθήκη στην οθόνη Αφετηρίας».</p>

<h2>Ιδιωτικότητα</h2>
<p>Χωρίς λογαριασμούς, cookies, analytics ή scripts τρίτων. Η αναζήτηση γίνεται στον browser σας.
Φιλοξενία: Cloudflare, που κρατά τεχνικά logs ασφαλείας.</p>

<h2>Ποιος</h2>
<p>Ανεξάρτητο project του Πιέρρου Παπαδέα. <a href="{REPO}">Ανοιχτός κώδικας</a> (AGPL-3.0), ελεύθερα δεδομένα (CC0).</p>

<h2>Λάθη</h2>
<p>Ανοίξτε <a href="{REPO}/issues">issue</a> με τον κωδικό ΕΟΦ και, αν γίνεται, το έγγραφο του Υπουργείου.</p>
</div>
"""
    return layout(
        ctx,
        "Σχετικά · farmaka.info",
        body,
        "/sxetika/",
        "Τι είναι το farmaka.info και από πού έρχονται τα δεδομένα.",
        nav="about",
    )


def substances_index(ctx: Context) -> str:
    """All substances A–Ω (Greek names first, then INNs), each with its package count."""
    items = []
    for slug, v in ctx.subst.items():
        name = v.get("name_el") or inn_label(v["name"])
        items.append((name, slug, v))
    items.sort(key=lambda t: (t[0][:1].isascii(), t[0].lower()))
    groups: dict[str, list] = {}
    for name, slug, v in items:
        groups.setdefault(name[:1].upper(), []).append((name, slug, v))
    parts = []
    for letter, rows in groups.items():
        lis = "".join(
            f'<li><a href="/ousia/{e(slug)}/">{e(name)}</a>'
            + (f' <span class="inn">{e(inn_label(v["name"]))}</span>' if v.get("name_el") else "")
            + f' <span class="n">{len(v["codes"])}</span></li>'
            for name, slug, v in rows
        )
        parts.append(f'<section class="letter"><h2>{e(letter)}</h2><ul class="az">{lis}</ul></section>')
    body = f"""
<div class="wrap">
<div class="page-head">
  <h1>Δραστικές ουσίες</h1>
  <p>{num(len(ctx.subst), 0)} ουσίες. Ελληνικά ονόματα από το Wikidata όπου υπάρχουν· αλλιώς το διεθνές όνομα (INN).</p>
</div>
{search_form(autofocus=False)}
{"".join(parts)}
</div>
"""
    return layout(
        ctx,
        "Δραστικές ουσίες · farmaka.info",
        body,
        "/ousies/",
        "Όλες οι δραστικές ουσίες φαρμάκων στην Ελλάδα.",
        search_box=False,
    )


def _fact_value(f: dict) -> str:
    v, k = f["value"], f["kind"]
    if k == "eur":
        return eur(v)
    if k == "pct":
        return f"{'−' if v < 0 else '+'}{num(abs(v), 0)}%"
    if k == "times":
        return f"×{num(v, 1)}"
    return num(v, 0)


def trivia_page(ctx: Context, facts: list[dict]) -> str:
    """The easter egg: curiosities computed from the data on every build."""
    cards = []
    for f in facts:
        links = "".join(f'<a class="chip" href="{e(h)}">{e(label)}</a>' for label, h in f["links"])
        cards.append(
            f'<section class="fact"><h2>{e(f["title"])}</h2><div class="fv">{_fact_value(f)}</div>'
            f"<p>{e(f['text'])}</p>{f'<div class=chips>{links}</div>' if links else ''}</section>"
        )
    body = f"""
<div class="wrap">
<div class="page-head">
  <h1>Περίεργα από τα δεδομένα</h1>
  <p>Όσα μας έκαναν να σηκώσουμε το φρύδι ψάχνοντας τους καταλόγους. Υπολογίζονται από τα ίδια στοιχεία με το
  υπόλοιπο site, σε κάθε ενημέρωση· τίποτα δεν γράφεται με το χέρι.</p>
</div>
<div class="facts-grid">{"".join(cards)}</div>
<p class="small">Βρήκατε κάτι ακόμη πιο περίεργο; <a href="{REPO}/issues">Πείτε μας</a>.</p>
</div>
"""
    return layout(
        ctx,
        "Περίεργα από τα δεδομένα · farmaka.info",
        body,
        "/perierga/",
        "Παράξενα ευρήματα από τους επίσημους καταλόγους φαρμάκων στην Ελλάδα.",
    )


def shortages_page(ctx: Context) -> str:
    m = ctx.meta
    groups = []
    for t in ("limited_availability", "shortage", "discontinuation", "eu_shortage"):
        ps = [p for p in ctx.by_code.values() if any(a["type"] == t for a in p.get("availability") or [])]
        if not ps:
            continue
        title, _, src = AVAIL[t]
        info = m.get("shortages", {}).get(t, {})
        lis = []
        for p in sorted(ps, key=lambda p: p["name"]):
            a = next(a for a in p["availability"] if a["type"] == t)
            why = EU_REASON.get(a.get("reason") or "", a.get("reason") or "")
            today = ctx.meta["built_at"][:10]
            span = ""
            if a.get("start"):
                span = ("αναμένεται " if a["start"] > today else "") + date_gr(a["start"])
                span += f" – {date_gr(a['valid_until'])}" if a.get("valid_until") else ""
            start = span
            lis.append(
                f'<li><a class="nm" href="/farmako/{e(p["eof_code"])}/">{name_html(p["name"])}</a>'
                f'<span class="mah">{e(why)}</span>{available_equivalents(ctx, p)}<span class="p">{start}</span></li>'
            )
        when = f" · {src} {date_gr(info['published'])}" if info.get("published") else f" · {src}"
        groups.append(
            f'<section class="group"><h2>{e(title)} <span>{len(ps)}{e(when)}</span></h2>'
            f'<ul class="rows">{"".join(lis)}</ul></section>'
        )
    empty = '<p class="empty">Δεν υπάρχουν τρέχουσες εγγραφές.</p>'
    body = f"""
<div class="wrap">
<div class="page-head">
  <h1>Ελλείψεις φαρμάκων</h1>
  <p>Οι επίσημες λίστες επάρκειας του ΕΟΦ, που ενημερώνονται περίπου μηνιαία, και ο κατάλογος ελλείψεων της EMA·
  όχι το απόθεμα των φαρμακείων. Η λίστα απαγόρευσης παράλληλων εξαγωγών λείπει, γιατί δημοσιεύεται μόνο σκαναρισμένη.</p>
</div>
<p class="note">Δίπλα σε κάθε φάρμακο: πόσα ισοδύναμα (ίδια ουσία, περιεκτικότητα και μορφή) δεν είναι σε λίστα
ελλείψεων ή διακοπής του ΕΟΦ. Η διαθεσιμότητα επιβεβαιώνεται στο φαρμακείο· η αλλαγή σκευάσματος γίνεται μόνο με τη
γνώμη γιατρού ή φαρμακοποιού.</p>
{"".join(groups) or empty}
</div>
"""
    return layout(
        ctx,
        "Ελλείψεις φαρμάκων · farmaka.info",
        body,
        "/elleipseis/",
        "Φάρμακα σε έλλειψη ή διακοπή στην Ελλάδα, από τις επίσημες λίστες του ΕΟΦ και της EMA.",
    )


CHANGES_LIMIT = 50  # rows per kind of change in one bulletin; all of them are in data/allages.csv
LIST_LIMIT = 20  # an EOF list longer than this shows only its size


def _change_row(ctx: Context, it: dict) -> str:
    code = it["eof_code"]
    name = name_html(it["name"]) if it["name"] else f"<b>κωδικός ΕΟΦ {e(code)}</b>"
    # withdrawn packages have no page any more
    nm = (
        f'<a class="nm" href="/farmako/{e(code)}/">{name}</a>'
        if code in ctx.by_code
        else f'<span class="nm">{name}</span>'
    )
    c = changes.category(it)
    if c in ("cut", "rise"):
        tail = (
            f'<span class="old">{eur(it["old"])}</span><span class="new">{eur(it["new"])}</span>'
            f'<span class="pill {"down" if c == "cut" else "up"}">{changes.pct_label(it["old"], it["new"])}</span>'
        )
    elif c in ("new", "reappeared"):
        tail = f'<span class="new">{eur(it["new"])}</span>'
    elif c == "withdrawn":
        tail = f'<span class="old" title="Τελευταία τιμή">{eur(it["old"])}</span>'
    else:
        tail = ""
    return f"<li>{nm}{tail}</li>"


def _change_doc(ctx: Context, d: dict) -> str:
    moh = d["publisher"] == "moh"
    if moh:
        what = "Υπουργείο Υγείας · " + ("πλήρης κατάλογος" if d["kind"] == "full" else "δελτίο τιμών")
    else:
        what = "ΕΟΦ · " + changes.LIST_TITLES[d["kind"]].lower()
    title = e(d["title"] or ("Δελτίο τιμών" if moh else changes.LIST_TITLES[d["kind"]]))
    src = f'<a href="{e(d["url"])}" rel="noopener">{title} ↗</a>' if d["url"] else f"<b>{title}</b>"
    parts = [f'<p class="bsrc">{src}<span>{what}</span></p>']
    if d["pending"]:
        parts.append(
            '<p class="small">Σε αναμονή: δεν έχει ενσωματωθεί ακόμη '
            '(<a href="/dedomena/#katastasi">κατάσταση δεδομένων</a>).</p>'
        )
    elif d["baseline"]:
        parts.append(
            '<p class="small">Ο πρώτος κατάλογος που έχουμε· δεν υπάρχει προηγούμενος για σύγκριση.</p>'
        )
    elif not d["items"]:
        parts.append('<p class="small">Καμία αλλαγή λιανικής τιμής.</p>')
    if not moh and len(d["items"]) > LIST_LIMIT:
        parts.append(
            f'<p class="small">{num(len(d["items"]), 0)} φάρμακα στη λίστα. '
            'Η τρέχουσα εικόνα: <a href="/elleipseis/">ελλείψεις</a>.</p>'
        )
        return f'<article class="bul">{"".join(parts)}</article>'
    for c in changes.CATEGORIES:
        its = [it for it in d["items"] if changes.category(it) == c]
        if not its:
            continue
        head = changes.TITLES[c][0] if c in changes.TITLES else "Φάρμακα"
        rows = "".join(_change_row(ctx, it) for it in its[:CHANGES_LIMIT])
        parts.append(f'<h3>{head} <span>{num(len(its), 0)}</span></h3><ul class="changes">{rows}</ul>')
        if len(its) > CHANGES_LIMIT:
            which = "μεγαλύτερες" if c in ("cut", "rise") else "πρώτες αλφαβητικά"
            parts.append(
                f'<p class="more">Εδώ οι {CHANGES_LIMIT} {which}· όλες οι {num(len(its), 0)} στο '
                '<a href="/data/allages.csv">allages.csv</a>.</p>'
            )
    return f'<article class="bul">{"".join(parts)}</article>'


def changes_page(ctx: Context, ch: dict) -> str:
    """/allages/: changes by publication date, newest first, under their bulletin or list."""
    days = ch["days"]
    chips = "".join(f'<a class="chip" href="#d-{g["date"]}">{date_gr(g["date"])[:5]}</a>' for g in days)
    secs = "".join(
        f'<section class="day" id="d-{g["date"]}"><h2>{date_gr(g["date"])}</h2>'
        f'<p class="small top">{e(changes.summary(g["docs"]))}</p>'
        + "".join(_change_doc(ctx, d) for d in g["docs"])
        + "</section>"
        for g in days
    )
    span = f" ({date_gr(ch['start'])} – {date_gr(ch['end'])})" if days else ""
    body = f"""
<div class="wrap narrow">
<div class="page-head">
  <h1>Τι άλλαξε</h1>
  <p>Αλλαγές τιμών, νέες συσκευασίες και όσες βγήκαν από το δελτίο τιμών του Υπουργείου Υγείας, και οι νέες λίστες
  ελλείψεων και διακοπών του ΕΟΦ, για τις {changes.WINDOW_DAYS} ημέρες έως το νεότερο στοιχείο{span}. Κάθε αλλαγή
  κάτω από το δελτίο ή τη λίστα από όπου προέρχεται.</p>
</div>
<p class="feeds">Οι ίδιες αλλαγές ως <a href="/allages/rss.xml">RSS</a> και <a href="/data/allages.csv">CSV</a>.</p>
<div class="chips days">{chips}</div>
{secs or '<p class="empty">Καμία αλλαγή.</p>'}
<p class="small">Ημερομηνία: η δημοσίευση του δελτίου ή η ημερομηνία της λίστας· η ισχύς μιας τιμής μπορεί να
διαφέρει.</p>
</div>
"""
    return layout(
        ctx,
        "Τι άλλαξε · farmaka.info",
        body,
        "/allages/",
        "Πρόσφατες αλλαγές τιμών, νέες συσκευασίες και λίστες ελλείψεων φαρμάκων στην Ελλάδα, από το Υπουργείο Υγείας "
        "και τον ΕΟΦ.",
    )


def not_found(ctx: Context) -> str:
    body = f"""
<div class="wrap narrow prose">
<h1>Δεν βρέθηκε</h1>
<p class="lead">Η σελίδα δεν υπάρχει ή η συσκευασία έφυγε από το δελτίο τιμών.</p>
{search_form(autofocus=False)}
</div>"""
    return layout(ctx, "Δεν βρέθηκε · farmaka.info", body, "/404.html", search_box=False)


def search_template(ctx: Context) -> str:
    """Filled in by web/worker.js for /anazitisi (works without JavaScript)."""
    body = f"""
<div class="wrap narrow">
<div class="page-head"><h1>Αναζήτηση</h1></div>
<form class="search search-big" action="/anazitisi" method="get" role="search">
  {ICON_SEARCH.format(s=22)}
  <label class="sr" for="q">Αναζήτηση φαρμάκου</label>
  <input id="q" name="q" type="search" value="{{{{QUERY}}}}" placeholder="Όνομα, δραστική ουσία ή barcode" autocomplete="off" spellcheck="false" enterkeyhint="search">
  <button type="submit"><span class="btn-t">Αναζήτηση</span>{ICON_SEARCH.format(s=22)}</button>
  <div class="suggest" hidden></div>
</form>
{{{{RESULTS}}}}
</div>
"""
    return layout(ctx, "Αναζήτηση · farmaka.info", body, "/anazitisi", search_box=False)


HEADERS = """/*
  Content-Security-Policy: default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin
  Permissions-Policy: camera=(self), geolocation=(), microphone=(), interest-cohort=()
  Cross-Origin-Opener-Policy: same-origin
/static/*
  Cache-Control: public, max-age=3600
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
    icons = [
        {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        {
            "src": "/static/icon-maskable-512.png",
            "sizes": "512x512",
            "type": "image/png",
            "purpose": "maskable",
        },
        {"src": "/favicon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"},
    ]
    shortcut_icon = [{"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"}]
    return json.dumps(
        {
            "id": "/",
            "name": "farmaka.info · Πληροφορίες φαρμάκων",
            "short_name": "farmaka",
            "description": "Πληροφορίες φαρμάκων στην Ελλάδα: τιμές, αποζημίωση, δραστικές ουσίες, επίσημα κείμενα.",
            "lang": "el",
            "dir": "ltr",
            "start_url": "/",
            "scope": "/",
            "display": "standalone",
            "orientation": "any",
            "background_color": "#ffffff",
            "theme_color": "#0a6b5b",
            "categories": ["medical", "health", "reference"],
            "icons": icons,
            "shortcuts": [
                {"name": "Αναζήτηση", "url": "/anazitisi", "icons": shortcut_icon},
                {"name": "Δραστικές ουσίες", "url": "/ousies/", "icons": shortcut_icon},
                {"name": "Δεδομένα & API", "url": "/dedomena/", "icons": shortcut_icon},
            ],
        },
        ensure_ascii=False,
        indent=1,
    )
