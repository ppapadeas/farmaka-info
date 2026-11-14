// farmaka.info edge worker. Static assets (pages, downloads) are served directly;
// this runs only for paths without a matching asset: the JSON API, the
// no-JavaScript search page and 404s. Everything it reads comes from the same
// static build (dist/), so there is no database and no state.
import { codeFromQuery, eur, parseIndex, search } from "./static/search-core.js";

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
    return json({ query: q, total, results: results.map(summary) });
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
    const { total, results: rows } = search(await getIndex(env), q, 200);
    const items = rows
      .map(
        (r) =>
          `<tr><td><a href="/farmako/${esc(r.eof_code)}/">${esc(r.name)}</a>${
            r.reimbursed ? ' <span class="badge ok sm" title="Στον κατάλογο αποζημίωσης">Α</span>' : ""
          }<br><span class="muted">${esc(r.substances.join(" + "))}</span></td><td>${esc(r.mah)}</td><td class="n">${esc(
            eur(r.price_retail),
          )}</td></tr>`,
      )
      .join("");
    results = total
      ? `<p class="results-count">${total} συσκευασίες${total > rows.length ? `, εμφανίζονται οι πρώτες ${rows.length}` : ""}.</p>
<div class="tw"><table class="list"><thead><tr><th scope="col">Συσκευασία</th><th scope="col">Κάτοχος άδειας</th><th scope="col" class="n">Λιανική</th></tr></thead><tbody>${items}</tbody></table></div>`
      : `<p>Δεν βρέθηκε συσκευασία για «${esc(q)}». Δοκιμάστε εμπορική ονομασία, δραστική ουσία ή κωδικό ΕΟΦ.</p>`;
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
