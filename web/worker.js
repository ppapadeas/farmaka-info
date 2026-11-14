// farmaka.info edge worker. Static assets (pages, downloads) are served directly;
// this runs only for paths without a matching asset: the JSON API, the
// no-JavaScript search page and 404s. Everything it reads comes from the same
// static build (dist/), so there is no database and no state.
import {
  codeFromQuery,
  eur,
  innLabel,
  parseIndex,
  parseSubstances,
  search,
  searchSubstances,
  splitName,
} from "./static/search-core.js";

const SHARDS = 256;
const JSON_HEADERS = {
  "content-type": "application/json; charset=utf-8",
  "access-control-allow-origin": "*",
  "cache-control": "public, max-age=600",
  "x-content-type-options": "nosniff",
};
const HTML_HEADERS = {
  "content-type": "text/html; charset=utf-8",
  "content-security-policy":
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
  "x-content-type-options": "nosniff",
  "referrer-policy": "strict-origin-when-cross-origin",
};

// Same as sitegen/build.py::shard_of.
export function shardOf(code) {
  let h = 0;
  for (const ch of code) h = (h * 31 + ch.charCodeAt(0)) % 65536;
  return (h % SHARDS).toString(16).padStart(2, "0");
}

let indexRows = null;
let substanceRows = null;
const shardCache = new Map();

async function asset(env, path) {
  const r = await env.ASSETS.fetch(new Request(`https://assets.local${path}`));
  if (!r.ok) throw new Error(`asset ${path}: ${r.status}`);
  return r;
}

async function getPackage(env, code) {
  const k = shardOf(code);
  if (!shardCache.has(k)) shardCache.set(k, await (await asset(env, `/api/pk/${k}.json`)).json());
  return shardCache.get(k)[code] ?? null;
}

async function getSubstances(env) {
  if (!substanceRows) substanceRows = parseSubstances(await (await asset(env, "/substance-index.tsv")).text());
  return substanceRows;
}

async function getIndex(env) {
  if (!indexRows) indexRows = parseIndex(await (await asset(env, "/search-index.tsv")).text());
  return indexRows;
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body, null, 1), { status, headers: JSON_HEADERS });
}

function notFoundJson(message) {
  return json({ error: "not_found", message }, 404);
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function summary(r) {
  return {
    eof_code: r.eof_code,
    barcode: r.barcode,
    name: r.name,
    substances: r.substances,
    mah: r.mah,
    price_retail: r.price_retail,
    non_reimbursed: r.non_reimbursed,
    reimbursed: r.reimbursed,
    url: `https://farmaka.info/farmako/${r.eof_code}/`,
    api: `https://farmaka.info/api/v1/packages/${r.eof_code}`,
  };
}

async function api(url, env) {
  const parts = url.pathname.replace(/\/+$/, "").split("/").slice(3); // after /api/v1
  const [what, arg] = parts;
  if (!what) {
    return json({
      name: "farmaka.info API",
      version: 1,
      docs: "https://farmaka.info/dedomena/#api",
      license: "Data CC0-1.0; code AGPL-3.0-or-later",
      endpoints: [
        "/api/v1/packages/{eof_code}",
        "/api/v1/barcode/{ean13}",
        "/api/v1/search?q={text}&limit={1..200}",
        "/api/v1/substances",
        "/api/v1/substances/{slug}",
        "/api/v1/meta",
      ],
    });
  }
  if (what === "meta") return new Response((await asset(env, "/api/meta.json")).body, { headers: JSON_HEADERS });
  if (what === "packages" && arg) {
    if (!/^\d{9}$/.test(arg)) return json({ error: "bad_request", message: "eof_code is 9 digits" }, 400);
    const p = await getPackage(env, arg);
    return p ? json(p) : notFoundJson(`no package with eof_code ${arg}`);
  }
  if (what === "barcode" && arg) {
    if (!/^\d{13}$/.test(arg)) return json({ error: "bad_request", message: "barcode is EAN-13" }, 400);
    const code = codeFromQuery(arg);
    const p = code ? await getPackage(env, code) : null;
    return p && p.barcode === arg ? json(p) : notFoundJson(`no package with barcode ${arg}`);
  }
  if (what === "search") {
    const q = (url.searchParams.get("q") || "").slice(0, 200);
    const limit = Math.min(Math.max(parseInt(url.searchParams.get("limit") || "50", 10) || 50, 1), 200);
    const { total, results } = search(await getIndex(env), q, limit);
    const subs = searchSubstances(await getSubstances(env), q, 5).map((x) => ({
      slug: x.slug,
      inn: x.inn,
      name_el: x.name_el,
      packages: x.packages,
      url: `https://farmaka.info/ousia/${x.slug}/`,
    }));
    return json({ query: q, total, substances: subs, results: results.map(summary) });
  }
  if (what === "substances") {
    const all = await (await asset(env, "/api/substances.json")).json();
    if (!arg) return json(all);
    return all[arg] ? json({ slug: arg, ...all[arg] }) : notFoundJson(`no substance ${arg}`);
  }
  return notFoundJson("unknown endpoint; see /api/v1");
}

async function searchPage(url, env) {
  const q = (url.searchParams.get("q") || "").trim().slice(0, 200);
  const code = codeFromQuery(q);
  if (code && (await getPackage(env, code))) {
    return Response.redirect(`${url.origin}/farmako/${code}/`, 302);
  }
  let results = "";
  if (q) {
    const subs = searchSubstances(await getSubstances(env), q, 5);
    const subHtml = subs.length
      ? `<section class="group"><h2>Δραστικές ουσίες</h2><ul class="rows">${subs
          .map(
            (x) =>
              `<li><a class="nm" href="/ousia/${esc(x.slug)}/"><b>${esc(x.name_el || innLabel(x.inn))}</b> <span>${esc(
                x.name_el ? innLabel(x.inn) : "",
              )}</span></a><span class="p">${x.packages} συσκευασίες</span></li>`,
          )
          .join("")}</ul></section>`
      : "";
    const { total, results: rows } = search(await getIndex(env), q, 200);
    const items = rows
      .map((r) => {
        const [brand, rest] = splitName(r.name);
        const tag = r.reimbursed ? ' <span class="tag" title="Στον κατάλογο αποζημίωσης">Α</span>' : "";
        return `<li><a class="nm" href="/farmako/${esc(r.eof_code)}/"><b>${esc(brand)}</b> <span>${esc(rest)}</span></a>${tag}<span class="mah">${esc(r.substances.join(" + "))}</span><span class="p">${esc(eur(r.price_retail))}</span></li>`;
      })
      .join("");
    results = subHtml + (total
      ? `<p class="count">${total} συσκευασίες${total > rows.length ? `, οι πρώτες ${rows.length}` : ""}</p><ul class="rows">${items}</ul>`
      : subs.length ? "" : `<p class="empty">Το «${esc(q)}» δεν υπάρχει στο δελτίο τιμών του Υπουργείου. Πολλά μη συνταγογραφούμενα (π.χ. Depon, Panadol) έχουν ελεύθερη τιμή και δεν περιλαμβάνονται. Δοκιμάστε τη δραστική ουσία, π.χ. <a href="/anazitisi?q=%CF%80%CE%B1%CF%81%CE%B1%CE%BA%CE%B5%CF%84%CE%B1%CE%BC%CF%8C%CE%BB%CE%B7">παρακεταμόλη</a>.</p>`);
  }
  const tpl = await (await asset(env, "/_templates/search.html")).text();
  const html = tpl.replace("{{QUERY}}", esc(q)).replace("{{RESULTS}}", results);
  return new Response(html, { headers: { ...HTML_HEADERS, "cache-control": "public, max-age=600" } });
}

async function notFound(env) {
  const r = await asset(env, "/404.html");
  return new Response(r.body, { status: 404, headers: HTML_HEADERS });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.hostname.startsWith("www.")) {
      url.hostname = url.hostname.slice(4);
      return Response.redirect(url.toString(), 301);
    }
    if (request.method === "OPTIONS") {
      return new Response(null, {
        headers: {
          "access-control-allow-origin": "*",
          "access-control-allow-methods": "GET, HEAD, OPTIONS",
          "access-control-max-age": "86400",
        },
      });
    }
    if (request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Method not allowed", { status: 405, headers: { allow: "GET, HEAD, OPTIONS" } });
    }
    try {
      if (url.pathname === "/api/v1" || url.pathname.startsWith("/api/v1/")) return await api(url, env);
      if (url.pathname === "/anazitisi" || url.pathname === "/anazitisi/") return await searchPage(url, env);
      return await notFound(env);
    } catch (err) {
      console.error(err);
      return json({ error: "internal", message: "temporary error, please retry" }, 500);
    }
  },
};
