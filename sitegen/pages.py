"""HTML for every page type. Plain functions returning strings; all data is
escaped with `e()`. No inline scripts or styles (the CSP forbids them); charts
are inline SVG drawn with presentation attributes only."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from html import escape
from pathlib import Path
from urllib.parse import quote

from .drug import dispensing_label, inn_label, short_mah, split_name, strength, unit_count
from .text import date_gr, eur, num

SITE = "https://farmaka.info"
REPO = "https://github.com/ppapadeas/farmaka-info"
STATIC = Path(__file__).resolve().parent.parent / "web" / "static"

ASSET_VERSION = hashlib.sha256(
    b"".join(p.read_bytes() for p in sorted(STATIC.iterdir()) if p.is_file())
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


def pct(new: float, old: float) -> str:
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
<meta name="theme-color" content="#0a6b5b">
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
  <div class="wrap">
    <span>Ενημερωτικό· δεν αντικαθιστά γιατρό ή φαρμακοποιό. Πηγή: Υπουργείο Υγείας.</span>
    <nav aria-label="Υποσέλιδο"><a href="/dedomena/">Δεδομένα CC0</a><a href="{REPO}">Κώδικας</a><a href="{REPO}/issues">Αναφορά λάθους</a></nav>
  </div>
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
  <button type="submit">Αναζήτηση</button>
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
        '<section class="wrap narrow recent"><div class="section-head"><h2>Πρόσφατες αλλαγές</h2></div>'
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
    <div><strong>{num(m["substances"], 0)}</strong><span>δραστικές ουσίες</span></div>
    <div><strong>{num(reimb, 0)}</strong><span>αποζημιώνονται</span></div>
  </div>
  {pending}
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
        if h["change"] == "withdrawn" or h["new"] is None:
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
    retail = r["retail_in_list"] or p["prices"]["retail"]
    rp = r["reimbursement_price"]
    diff = max(retail - rp, 0)
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
    <div><i class="a"></i><p><strong>{eur(rp)}</strong><span>τιμή αποζημίωσης, όση η λιανική</span></p></div>
  </div>"""
    restr = (
        '<details class="restr"><summary>Περιορισμοί αποζημίωσης</summary>'
        + "".join(f"<p>{e(x)}</p>" for x in r["restrictions"])
        + "</details>"
        if r["restrictions"]
        else ""
    )
    lst = "" if r["list"] == "main" else f" · {e(LIST_LABELS.get(r['list'], r['list']))}"
    return f"""<section class="card">
  <div class="reimb-head"><h2>Αποζημίωση</h2>{head}</div>
  {bar}
  {legend}
  <p class="small">Συν τη συμμετοχή (0, 10 ή 25%) επί της τιμής αποζημίωσης{lst}.</p>
  {restr}
</section>"""


def equivalents_card(ctx: Context, p: dict) -> str:
    codes = ctx.equiv.get(p["eof_code"], [])
    if len(codes) < 2:
        return ""
    st = strength(p["name"])
    rows = [ctx.by_code[c] for c in codes]
    counts = {q["eof_code"]: unit_count(q["name"]) for q in rows}
    per_unit = all(counts.values())

    def key(q: dict) -> float:
        return q["prices"]["retail"] / counts[q["eof_code"]] if per_unit else q["prices"]["retail"]

    rows.sort(key=lambda q: (key(q), q["prices"]["retail"], q["name"]))
    mine = key(p)
    cheaper = [q for q in rows if key(q) < mine * 0.995]
    shown = rows[:7]
    if p not in shown:
        shown.append(p)
    lis = []
    for q in shown:
        n = counts[q["eof_code"]]
        extra = " · ".join(x for x in ((f"×{n}" if n else ""), short_mah(q["mah"])) if x)
        label = f"<b>{e(split_name(q['name'])[0])}</b> <span>· {e(extra)}</span>"
        u = f'<span class="u">{eur(key(q))}/τεμ.</span>' if per_unit else ""
        price = f'<span class="p">{eur(q["prices"]["retail"])}</span>'
        if q is p:
            lis.append(
                f'<li class="me"><span class="nm">{label}</span>{u}{price}<span class="pill here">αυτό</span></li>'
            )
            continue
        k = key(q)
        rel = (
            f'<span class="pill {"down" if k < mine else "up"}">{pct(k, mine)}</span>'
            if abs(k - mine) / mine > 0.005
            else '<span class="pill same">ίδια</span>'
        )
        lis.append(f'<li><a class="nm" href="/farmako/{e(q["eof_code"])}/">{label}</a>{u}{price}{rel}</li>')
    basis = "ανά τεμάχιο" if per_unit else "ανά συσκευασία"
    if cheaper:
        best = (mine - key(cheaper[0])) / mine * 100
        word = "φθηνότερη" if len(cheaper) == 1 else "φθηνότερες"
        summary = f"{len(cheaper)} {word} {basis}, έως <b>{num(best, 0)}%</b> λιγότερο."
    else:
        summary = f"Η φθηνότερη επιλογή {basis}."
    subs = " + ".join(inn_label(s) for s in p["substances"])
    more = (
        f'<a href="/ousia/{e(ctx.slug_of[p["substances"][0]])}/">Όλες οι {len(rows)}</a>'
        if len(rows) > len(shown)
        else ""
    )
    return f"""<section class="card list-card">
  <div class="section-head"><h2>{e(subs)} {e(st[1] if st else "")}</h2>{more}</div>
  <p class="small top">{summary}</p>
  <ul class="rows eq">{"".join(lis)}</ul>
  <p class="small">Ίδια δραστική ουσία, περιεκτικότητα και μορφή. Αλλαγή σκευάσματος μόνο με τη γνώμη γιατρού ή φαρμακοποιού.</p>
</section>"""


def package(ctx: Context, p: dict) -> str:
    pr = p["prices"]
    brand, rest = split_name(p["name"])
    subs = p["substances"]
    crumbs = (
        f'<a href="/ousia/{e(ctx.slug_of[subs[0]])}/">{e(inn_label(subs[0]))}</a>' if subs else "Συσκευασία"
    )
    chips = [
        f'<a class="chip sub-link" href="/ousia/{e(ctx.slug_of[s])}/">{e(inn_label(s))}</a>' for s in subs
    ]
    if p["mah"]:
        chips.append(f'<span class="chip" title="{e(p["mah"])}">{e(short_mah(p["mah"]))}</span>')
    disp = dispensing_label(p["reimbursement"][0]["dispensing"]) if p["reimbursement"] else None
    if disp:
        chips.append(f'<span class="chip">{e(disp)}</span>')
    if p["non_reimbursed"]:
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
        f'<li><a href="{e(ps["url"])}" rel="noopener">Δελτίο τιμών ↗</a><div>Υπουργείο Υγείας · {date_gr(ps["published"])}</div></li>'
    ]
    if p["reimbursement"]:
        rs = p["reimbursement"][0]["source"]
        srcs.append(
            f'<li><a href="{e(rs["url"])}" rel="noopener">Κατάλογος αποζημίωσης ↗</a><div>Υπουργείο Υγείας · {date_gr(rs["published"])}</div></li>'
        )
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
    <section class="card price">
      <div><div class="label">Λιανική τιμή</div><div class="big">{eur(pr["retail"])}</div>{was}</div>
      <dl class="kv">
        <dt>Χονδρική</dt><dd>{eur(pr["wholesale"])}</dd>
        <dt>Παραγωγού</dt><dd>{eur(pr["exfactory"])}</dd>
        <dt>ΦΠΑ</dt><dd>{vat}</dd>
      </dl>
    </section>
    {reimbursement_card(ctx, p)}
    {equivalents_card(ctx, p)}
    {history_card(ctx, p)}
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
    <section class="card side">
      <h2>Πηγές</h2>
      <ul class="srcs">{"".join(srcs)}</ul>
    </section>
    <div class="links"><a href="/api/v1/packages/{e(p["eof_code"])}">JSON</a><a href="{REPO}/issues/new?title={issue}">Αναφορά λάθους</a></div>
  </aside>
</div>
</div>
"""
    subst = ", ".join(inn_label(s) for s in subs) or "—"
    return layout(
        ctx,
        f"{brand} {rest} · {eur(pr['retail'])} · farmaka.info",
        body,
        f"/farmako/{p['eof_code']}/",
        f"{p['name']} ({subst}). Λιανική {eur(pr['retail'])}, αποζημίωση, φθηνότερα ισοδύναμα και ιστορικό τιμής, "
        "από το Υπουργείο Υγείας.",
    )


# ---------------------------------------------------------------- substance


def _row(p: dict) -> str:
    tag = ' <span class="tag" title="Στον κατάλογο αποζημίωσης">Α</span>' if p["reimbursement"] else ""
    return (
        f'<li><a class="nm" href="/farmako/{e(p["eof_code"])}/">{name_html(p["name"])}</a>{tag}'
        f'<span class="mah">{e(short_mah(p["mah"]))}</span><span class="p">{eur(p["prices"]["retail"])}</span></li>'
    )


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
        title = " + ".join(inn_label(n) for n in names)
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
  <h1>{e(inn_label(s["name"]))}</h1>
  <p>{num(len(rows), 0)} συσκευασίες · {num(reimb, 0)} αποζημιώνονται · ATC {e(", ".join(atcs[:4]) or "—")}</p>
</div>
{"".join(parts)}
</div>
"""
    return layout(
        ctx,
        f"{inn_label(s['name'])} · συσκευασίες και τιμές · farmaka.info",
        body,
        f"/ousia/{s['slug']}/",
        f"Όλες οι συσκευασίες με {inn_label(s['name'])} στην Ελλάδα, με τιμή και αποζημίωση.",
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
  <button type="submit">Αναζήτηση</button>
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
    return json.dumps(
        {
            "name": "farmaka.info",
            "short_name": "farmaka",
            "description": "Πληροφορίες φαρμάκων στην Ελλάδα",
            "lang": "el",
            "start_url": "/",
            "display": "standalone",
            "background_color": "#ffffff",
            "theme_color": "#0a6b5b",
            "icons": [{"src": "/favicon.svg", "sizes": "any", "type": "image/svg+xml"}],
        },
        ensure_ascii=False,
        indent=1,
    )
