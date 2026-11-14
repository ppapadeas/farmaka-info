// Offline support for farmaka.info. Served from the site root (/sw.js) so that
// its scope is the whole site. No libraries, no third-party requests.
//
// - pages: network first, cached copy when offline; every page opened is kept
//   so that packages viewed before still open without a connection
// - /static/*, /search-index.tsv, /substance-index.tsv: stale-while-revalidate
// - /api/* and /anazitisi: never cached

const VERSION = "2";
const PREFIX = "farmaka-";
const ASSETS = `${PREFIX}assets-v${VERSION}`;
const PAGES = `${PREFIX}pages-v${VERSION}`;
const MAX_PAGES = 300;
const VERSION_HEADER = "x-farmaka-asset-version";

const PRECACHE_PAGES = ["/", "/ousies/"];
const PRECACHE_ASSETS = [
  "/static/style.css",
  "/static/app.js",
  "/static/search-core.js",
  "/static/scan.js",
  "/static/scan.css",
  "/static/register-sw.js",
  "/static/inter-latin.woff2",
  "/static/inter-greek.woff2",
  "/favicon.svg",
  "/manifest.webmanifest",
  "/search-index.tsv",
  "/substance-index.tsv",
  "/static/icon-192.png",
  "/static/apple-touch-icon.png",
];

const OFFLINE_HTML = `<!doctype html>
<html lang="el">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Χωρίς σύνδεση · farmaka.info</title>
<link rel="stylesheet" href="/static/style.css">
</head>
<body>
<main id="main" class="wrap">
<h1>Δεν υπάρχει σύνδεση</h1>
<p>Αυτή η σελίδα δεν έχει αποθηκευτεί στη συσκευή. Όσες σελίδες έχετε ανοίξει πριν είναι διαθέσιμες και χωρίς σύνδεση.</p>
<p><a href="/">Αρχική σελίδα</a></p>
</main>
</body>
</html>
`;

// Asset URLs carry ?v=<hash of all static files>. The cache key is the path
// without the query; the hash is stored with the response so that a page from
// a newer build does not get old scripts. Modules imported without ?v (e.g.
// ./search-core.js) are checked against the newest hash seen in this worker.
let latestAssetVersion = "";

function keyOf(url) {
  return url.origin + url.pathname;
}

function withVersion(res, version) {
  const headers = new Headers(res.headers);
  headers.set(VERSION_HEADER, version);
  return new Response(res.body, { status: res.status, statusText: res.statusText, headers });
}

async function precache(cacheName, paths) {
  const cache = await caches.open(cacheName);
  // Best effort: one missing file must not leave the whole site without offline support.
  await Promise.allSettled(
    paths.map(async (p) => {
      const res = await fetch(new Request(p, { cache: "no-cache" }));
      if (res.ok) await cache.put(new URL(p, self.location.origin).href, withVersion(res, ""));
    }),
  );
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    Promise.all([precache(PAGES, PRECACHE_PAGES), precache(ASSETS, PRECACHE_ASSETS)]).then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((names) =>
        Promise.all(names.filter((n) => n.startsWith(PREFIX) && n !== ASSETS && n !== PAGES).map((n) => caches.delete(n))),
      )
      .then(() => self.clients.claim()),
  );
});

async function trimPages(cache) {
  const keys = await cache.keys(); // oldest first; a re-visited page moves to the end
  const extra = keys.filter((r) => new URL(r.url).pathname !== "/").slice(0, Math.max(0, keys.length - MAX_PAGES));
  await Promise.all(extra.map((r) => cache.delete(r)));
}

async function page(event, url, store) {
  const key = keyOf(url);
  try {
    const res = await fetch(event.request);
    const html = (res.headers.get("content-type") || "").includes("text/html");
    if (store && res.ok && res.type === "basic" && html) {
      const copy = res.clone();
      event.waitUntil(
        caches.open(PAGES).then(async (cache) => {
          await cache.put(key, copy);
          await trimPages(cache);
        }),
      );
    }
    return res;
  } catch (err) {
    const cached = store ? await caches.match(key, { cacheName: PAGES }) : undefined;
    if (cached) return cached;
    return new Response(OFFLINE_HTML, {
      status: 503,
      headers: {
        "content-type": "text/html; charset=utf-8",
        "cache-control": "no-store",
        "content-security-policy": "default-src 'self'; script-src 'none'; object-src 'none'; base-uri 'none'",
      },
    });
  }
}

async function asset(event, url) {
  const key = keyOf(url);
  const isStatic = url.pathname.startsWith("/static/");
  let want = "";
  if (isStatic) {
    want = url.searchParams.get("v") || latestAssetVersion;
    if (url.searchParams.get("v")) latestAssetVersion = want;
  }
  const cache = await caches.open(ASSETS);
  const cached = await cache.match(key);
  const fresh = cached && (!isStatic || !want || cached.headers.get(VERSION_HEADER) === want);
  // On a version change, skip the HTTP cache too: unversioned module URLs may
  // still hold the previous build there (Cache-Control max-age on /static/*).
  const req = fresh ? event.request : new Request(event.request, { cache: "no-cache" });
  const network = fetch(req).then(async (res) => {
    if (res.ok && res.type === "basic") await cache.put(key, withVersion(res.clone(), want));
    return res;
  });
  if (fresh) {
    event.waitUntil(network.catch(() => {}));
    return cached;
  }
  try {
    return await network;
  } catch (err) {
    if (cached) return cached;
    throw err;
  }
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname === "/api" || url.pathname.startsWith("/api/")) return;
  const search = url.pathname === "/anazitisi" || url.pathname === "/anazitisi/";

  if (req.mode === "navigate") {
    event.respondWith(page(event, url, !search));
    return;
  }
  if (search) return;
  if (url.pathname.startsWith("/static/") || url.pathname.endsWith("-index.tsv") || PRECACHE_ASSETS.includes(url.pathname)) {
    event.respondWith(asset(event, url));
  }
});
