// Search shared by the browser (static/app.js) and the API worker (web/worker.js).
//
// Names in the price list are mostly Latin capitals (brand + form + strength),
// substances are English INNs, people type Greek, Latin or greeklish, with or
// without accents. Both the index and the query are folded to the same rough
// phonetic "skeleton". sitegen/text.py::skeleton must stay identical to this;
// tests/skeleton_vectors.json is checked against both.

const GREEK_DIGRAPHS = [
  ["ου", "u"],
  ["αι", "e"],
  ["ει", "i"],
  ["οι", "i"],
  ["υι", "i"],
  ["αυ", "av"],
  ["ευ", "ev"],
  ["μπ", "mp"],
  ["ντ", "nt"],
  ["γκ", "g"],
  ["γγ", "g"],
  ["τσ", "ts"],
  ["τζ", "tz"],
];

const GREEK = {
  α: "a", β: "v", γ: "g", δ: "d", ε: "e", ζ: "z", η: "i", θ: "th", ι: "i", κ: "k",
  λ: "l", μ: "m", ν: "n", ξ: "ks", ο: "o", π: "p", ρ: "r", σ: "s", ς: "s", τ: "t",
  υ: "i", φ: "f", χ: "ch", ψ: "ps", ω: "o",
};

const LATIN = [
  ["ph", "f"],
  ["h", ""],
  ["c", "k"],
  ["q", "k"],
  ["x", "ks"],
  ["w", "v"],
  ["j", "i"],
  ["y", "i"],
  ["mp", "v"],
  ["b", "v"],
  ["nt", "d"],
  ["ou", "u"],
  ["e", "i"],
];

export function skeleton(text) {
  let s = String(text ?? "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
  for (const [a, b] of GREEK_DIGRAPHS) s = s.split(a).join(b);
  s = s.replace(/[α-ω]/g, (c) => GREEK[c] ?? "");
  for (const [a, b] of LATIN) s = s.split(a).join(b);
  return s
    .replace(/[^a-z0-9]+/g, " ")
    .replace(/([a-z])\1+/g, "$1")
    .trim();
}

// Greek words carry endings the INN does not (παρακεταμόλη / paracetamol):
// long tokens lose one trailing vowel.
export function queryTokens(q) {
  return skeleton(q)
    .split(" ")
    .filter(Boolean)
    .map((t) => (t.length >= 5 ? t.replace(/[aiou]$/, "") : t));
}

// Index: one package per line, tab separated, see sitegen/build.py::write_search_index.
export const COLUMNS = [
  "eof_code", "barcode", "name", "substances", "mah", "price_retail",
  "non_reimbursed", "reimbursed", "sk_name", "sk_subst", "sk_mah",
];

export function parseIndex(tsv) {
  const rows = [];
  for (const line of tsv.split("\n")) {
    if (!line || line.startsWith("#")) continue;
    const f = line.split("\t");
    rows.push({
      eof_code: f[0],
      barcode: f[1],
      name: f[2],
      substances: f[3] ? f[3].split("|") : [],
      mah: f[4],
      price_retail: Number(f[5]),
      non_reimbursed: f[6] === "1",
      reimbursed: f[7] === "1",
      sk_name: f[8],
      sk_subst: f[9],
      sk_mah: f[10],
      hay: `${f[8]} ${f[9]} ${f[10]} ${f[0]} ${f[1]}`,
    });
  }
  return rows;
}

// Codes: 9 digits = EOF code, 13 digits starting 280 = barcode embedding it.
export function codeFromQuery(q) {
  const d = String(q).replace(/\s+/g, "");
  if (/^\d{9}$/.test(d)) return d;
  if (/^280\d{10}$/.test(d)) return d.slice(3, 12);
  return null;
}

function rank(r, first) {
  if (r.sk_name.startsWith(first)) return 0;
  if ((" " + r.sk_name).includes(" " + first)) return 1;
  if (r.sk_subst.startsWith(first) || (" " + r.sk_subst).includes(" " + first)) return 2;
  return 3;
}

export function search(rows, q, limit = 50) {
  const tokens = queryTokens(q);
  if (!tokens.length) return { total: 0, results: [] };
  const hits = [];
  for (const r of rows) {
    if (tokens.every((t) => r.hay.includes(t))) hits.push([rank(r, tokens[0]), r]);
  }
  hits.sort((a, b) => a[0] - b[0] || a[1].name.localeCompare(b[1].name, "el"));
  return { total: hits.length, results: hits.slice(0, limit).map((h) => h[1]) };
}

export function eur(x) {
  return new Intl.NumberFormat("el-GR", { style: "currency", currency: "EUR" }).format(x);
}

// Same rule as sitegen/drug.py::split_name: brand = words before the dose form.
const FORM_WORDS = new Set([
  "TABLET", "TABLETS", "TABS", "TAB", "CAPS", "CAP", "SYR", "CREAM", "GEL", "OINT", "LOTION", "POWDER",
  "DROPS", "SUPP", "PATCH", "SHAMPOO", "SPRAY", "GRANULES", "EMULSION", "SUSPENSION", "SOLUTION",
]);
const DOTTED_FORM = /^(?=.*[A-Z])[A-Z/]+(?:\.[A-Z/]+)+\.?$/;

export function splitName(name) {
  const toks = String(name).split(/\s+/).filter(Boolean);
  for (let i = 1; i < toks.length; i++) {
    const t = toks[i].toUpperCase().replace(/,+$/, "");
    if (FORM_WORDS.has(t) || DOTTED_FORM.test(t)) {
      return [toks.slice(0, i).join(" ").replace(/[®™]/g, "").trim(), toks.slice(i).join(" ")];
    }
  }
  return [(toks[0] || name).replace(/[®™]/g, ""), toks.slice(1).join(" ")];
}
