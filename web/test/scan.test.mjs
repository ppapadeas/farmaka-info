import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import {
  HINT,
  NOT_MEDICINE,
  cameraError,
  ean13Valid,
  initScan,
  isSupported,
  packageUrl,
  pickCode,
} from "../static/scan.js";
import { registerServiceWorker } from "../static/register-sw.js";

// --- scan.js ---

test("scan module loads without a browser and adds nothing", async () => {
  assert.equal(typeof globalThis.window, "undefined");
  assert.equal(isSupported(), false);
  assert.equal(await initScan(undefined), false);
  assert.equal(await initScan({}), false);
  assert.ok(HINT && NOT_MEDICINE);
});

test("feature detection needs both BarcodeDetector and getUserMedia", () => {
  const camera = { mediaDevices: { getUserMedia() {} } };
  assert.equal(isSupported({ BarcodeDetector: class {}, navigator: camera }), true);
  assert.equal(isSupported({ navigator: camera }), false);
  assert.equal(isSupported({ BarcodeDetector: class {}, navigator: {} }), false);
  assert.equal(isSupported({ BarcodeDetector: class {} }), false);
  assert.equal(isSupported(null), false);
});

test("EAN-13 check digit", () => {
  assert.equal(ean13Valid("2803120801018"), true);
  assert.equal(ean13Valid("4006381333931"), true);
  assert.equal(ean13Valid("2803120801017"), false);
  assert.equal(ean13Valid("280312080101"), false);
  assert.equal(ean13Valid("abcdefghijklm"), false);
});

test("pickCode takes the first medicine barcode of a frame", () => {
  const med = { format: "ean_13", rawValue: "2803120801018" };
  const other = { format: "ean_13", rawValue: "4006381333931" };
  assert.deepEqual(pickCode([med]), { code: "312080101", foreign: false });
  assert.deepEqual(pickCode([other, med]), { code: "312080101", foreign: false });
  assert.deepEqual(pickCode([other]), { code: null, foreign: true });
});

test("pickCode ignores misreads and other formats", () => {
  assert.deepEqual(pickCode([]), { code: null, foreign: false });
  assert.deepEqual(pickCode(undefined), { code: null, foreign: false });
  assert.deepEqual(pickCode([{ format: "ean_13", rawValue: "2803120801017" }]), { code: null, foreign: false });
  assert.deepEqual(pickCode([{ format: "qr_code", rawValue: "2803120801018" }]), { code: null, foreign: false });
  assert.deepEqual(pickCode([{ format: "ean_13", rawValue: " 2803120801018 " }]).code, "312080101");
  // a format-less result (some implementations) is still checked
  assert.deepEqual(pickCode([{ rawValue: "2803120801018" }]).code, "312080101");
});

test("package URL and camera messages", () => {
  assert.equal(packageUrl("312080101"), "/farmako/312080101/");
  assert.match(cameraError("NotAllowedError"), /άδεια/);
  assert.match(cameraError("NotFoundError"), /κάμερα/);
  assert.equal(cameraError("Whatever"), cameraError(undefined));
});

// --- register-sw.js ---

test("service worker registration is skipped without support", () => {
  assert.equal(registerServiceWorker({}), false);
  assert.equal(registerServiceWorker(undefined), false);
});

// --- sw.js, run with stub browser APIs ---

const ORIGIN = "https://farmaka.info";
const SW_SOURCE = readFileSync(new URL("../static/sw.js", import.meta.url), "utf8");

class FakeCache {
  constructor() {
    this.m = new Map();
  }
  async match(k) {
    return this.m.get(typeof k === "string" ? k : k.url)?.clone();
  }
  async put(k, res) {
    const key = typeof k === "string" ? k : k.url;
    this.m.delete(key); // re-put moves to the end, like the Cache API
    this.m.set(key, res);
  }
  async keys() {
    return [...this.m.keys()].map((url) => ({ url }));
  }
  async delete(k) {
    return this.m.delete(typeof k === "string" ? k : k.url);
  }
}

function loadSw({ online = true, body = "net" } = {}) {
  const stores = new Map();
  const handlers = {};
  const fetched = [];
  const state = { online, body };
  const caches = {
    stores,
    async open(name) {
      if (!stores.has(name)) stores.set(name, new FakeCache());
      return stores.get(name);
    },
    async match(k, opts = {}) {
      const names = opts.cacheName ? [opts.cacheName] : [...stores.keys()];
      for (const n of names) {
        const r = await stores.get(n)?.match(k);
        if (r) return r;
      }
      return undefined;
    },
    async keys() {
      return [...stores.keys()];
    },
    async delete(n) {
      return stores.delete(n);
    },
  };
  class StubRequest {
    constructor(input, init = {}) {
      this.url = typeof input === "string" ? new URL(input, ORIGIN).href : input.url;
      this.method = input.method ?? "GET";
      this.mode = input.mode ?? "cors";
      this.cache = init.cache ?? input.cache ?? "default";
    }
  }
  async function fetch(req) {
    fetched.push({ url: req.url, cache: req.cache ?? "default" });
    if (!state.online) throw new TypeError("offline");
    const res = new Response(state.body, { status: 200, headers: { "content-type": "text/html; charset=utf-8" } });
    Object.defineProperty(res, "type", { value: "basic" });
    return res;
  }
  const self = {
    location: { origin: ORIGIN },
    addEventListener(type, fn) {
      handlers[type] = fn;
    },
    skipWaiting() {},
    clients: { claim() {} },
  };
  vm.runInNewContext(SW_SOURCE, { self, caches, fetch, Request: StubRequest, Response, Headers, URL, console });

  async function dispatch(path, { mode = "cors", method = "GET" } = {}) {
    const waits = [];
    let responded = null;
    const request = { url: new URL(path, ORIGIN).href, mode, method };
    handlers.fetch({
      request,
      respondWith(p) {
        responded = p;
      },
      waitUntil(p) {
        waits.push(p);
      },
    });
    const res = responded ? await responded : null;
    await Promise.all(waits);
    return res;
  }
  return { handlers, caches, fetched, state, dispatch };
}

test("sw: api, search, POST and other origins are left to the network", async () => {
  const sw = loadSw();
  assert.equal(await sw.dispatch("/api/v1/meta"), null);
  assert.equal(await sw.dispatch("/api/v1/packages/312080101", { mode: "navigate" }), null);
  assert.equal(await sw.dispatch("/anazitisi?q=depon"), null);
  assert.equal(await sw.dispatch("/static/app.js", { method: "POST" }), null);
  assert.equal(await sw.dispatch("https://example.org/static/app.js"), null);
  assert.equal(sw.fetched.length, 0);
});

test("sw: visited pages are kept and served offline", async () => {
  const sw = loadSw({ body: "<p>package</p>" });
  const online = await sw.dispatch("/farmako/312080101/", { mode: "navigate" });
  assert.equal(await online.text(), "<p>package</p>");
  sw.state.online = false;
  const offline = await sw.dispatch("/farmako/312080101/", { mode: "navigate" });
  assert.equal(offline.status, 200);
  assert.equal(await offline.text(), "<p>package</p>");
});

test("sw: search pages are not kept; unknown pages offline get a short notice", async () => {
  const sw = loadSw();
  await sw.dispatch("/anazitisi?q=depon", { mode: "navigate" });
  assert.equal(sw.caches.stores.get("farmaka-pages-v1")?.m.size ?? 0, 0);
  sw.state.online = false;
  for (const path of ["/anazitisi?q=depon", "/farmako/000000000/"]) {
    const res = await sw.dispatch(path, { mode: "navigate" });
    assert.equal(res.status, 503);
    assert.match(await res.text(), /Δεν υπάρχει σύνδεση/);
  }
});

test("sw: assets are keyed without ?v and refreshed when the build changes", async () => {
  const sw = loadSw({ body: "build-a" });
  // first sight: network, stored under the bare path
  assert.equal(await (await sw.dispatch("/static/app.js?v=a")).text(), "build-a");
  const assets = sw.caches.stores.get("farmaka-assets-v1");
  assert.ok(assets.m.has(`${ORIGIN}/static/app.js`));
  assert.equal(assets.m.has(`${ORIGIN}/static/app.js?v=a`), false);

  // same build: cached copy now, revalidated in the background
  sw.state.body = "build-a2";
  assert.equal(await (await sw.dispatch("/static/app.js?v=a")).text(), "build-a");
  assert.equal(await (await sw.dispatch("/static/app.js?v=a")).text(), "build-a2");

  // unversioned module import of the same build: cached copy
  await sw.dispatch("/static/search-core.js");
  sw.state.body = "core-a2";
  assert.equal(await (await sw.dispatch("/static/search-core.js")).text(), "build-a2");

  // new build: the network, bypassing the HTTP cache, for versioned and unversioned URLs
  sw.state.body = "build-b";
  sw.fetched.length = 0;
  assert.equal(await (await sw.dispatch("/static/app.js?v=b")).text(), "build-b");
  assert.equal(await (await sw.dispatch("/static/search-core.js")).text(), "build-b");
  assert.deepEqual(sw.fetched.map((f) => f.cache), ["no-cache", "no-cache"]);

  // offline: whatever is cached
  sw.state.online = false;
  assert.equal(await (await sw.dispatch("/static/app.js?v=c")).text(), "build-b");
  await assert.rejects(sw.dispatch("/search-index.tsv")); // never fetched: a plain network error
});

test("sw: activate removes caches of older versions only", async () => {
  const sw = loadSw();
  for (const n of ["farmaka-assets-v0", "farmaka-pages-v0", "farmaka-assets-v1", "other-app"]) await sw.caches.open(n);
  let done;
  sw.handlers.activate({ waitUntil: (p) => (done = p) });
  await done;
  assert.deepEqual((await sw.caches.keys()).sort(), ["farmaka-assets-v1", "other-app"]);
});
