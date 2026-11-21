// Instant search suggestions. Without JavaScript the forms still work: they
// submit to /anazitisi, rendered by the worker.
import {
  codeFromQuery,
  eur,
  innLabel,
  parseIndex,
  parseSubstances,
  search,
  searchSubstances,
  skeleton,
  splitName,
} from "./search-core.js";
import { initScan } from "./scan.js";
import "./register-sw.js";

// Same build version as this module (?v=… added by sitegen), so the indexes
// a page loads always match the code that parses them.
const V = new URL(import.meta.url).searchParams.get("v");
const versioned = (path) => (V ? `${path}?v=${V}` : path);

let indexPromise = null;
// Both indexes load on first focus: packages and substances.
function loadIndex() {
  if (!indexPromise) {
    const get = (url, parse) =>
      fetch(url).then((r) => {
        if (!r.ok) throw new Error(`${url}: ${r.status}`);
        return r.text().then(parse);
      });
    indexPromise = Promise.all([
      get(versioned("/search-index.tsv"), parseIndex),
      get(versioned("/substance-index.tsv"), parseSubstances).catch(() => []),
    ]).catch((err) => {
      indexPromise = null;
      throw err;
    });
  }
  return indexPromise;
}

function el(tag, attrs = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  for (const c of children) n.append(c);
  return n;
}

function setup(form) {
  const input = form.querySelector("input[name=q]");
  const box = form.querySelector(".suggest");
  if (!input || !box) return;
  const listId = `${input.id || "q"}-list`;
  box.id = listId;
  box.setAttribute("role", "listbox");
  input.setAttribute("role", "combobox");
  input.setAttribute("aria-autocomplete", "list");
  input.setAttribute("aria-controls", listId);
  input.setAttribute("aria-expanded", "false");
  let active = -1;
  let timer = 0;

  const close = () => {
    box.hidden = true;
    input.setAttribute("aria-expanded", "false");
    active = -1;
  };
  const items = () => [...box.querySelectorAll("a")];
  const highlight = (i) => {
    const a = items();
    a.forEach((x, j) => x.setAttribute("aria-selected", String(j === i)));
    active = i;
    if (a[i]) a[i].scrollIntoView({ block: "nearest" });
  };

  async function render() {
    const q = input.value.trim();
    if (q.length < 2) return close();
    let rows, subs;
    try {
      [rows, subs] = await loadIndex();
    } catch {
      return close();
    }
    if (input.value.trim() !== q) return;
    const subHits = searchSubstances(subs, q, 3);
    const sk = skeleton(q);
    const egg = sk.startsWith("pirirg") || sk === "trivia" || sk === "iastir ig";
    const { total, results } = search(rows, q, subHits.length ? 6 : 8);
    box.replaceChildren();
    if (egg) {
      box.append(
        el(
          "a",
          { href: "/perierga/", role: "option", "aria-selected": "false", class: "egg-hit" },
          el(
            "span",
            { class: "t" },
            el("span", { class: "nm" }, el("b", {}, "Περίεργα από τα δεδομένα")),
            el("span", { class: "sub" }, "Το βρήκατε. Ό,τι πιο παράξενο κρύβουν οι κατάλογοι."),
          ),
        ),
      );
    }
    if (subHits.length) {
      box.append(el("div", { class: "grp" }, "Δραστικές ουσίες"));
      for (const s of subHits) {
        const main = s.name_el || innLabel(s.inn);
        box.append(
          el(
            "a",
            { href: `/ousia/${s.slug}/`, role: "option", "aria-selected": "false", class: "sub-hit" },
            el(
              "span",
              { class: "t" },
              el("span", { class: "nm" }, el("b", {}, main), " ", el("span", {}, s.name_el ? innLabel(s.inn) : "")),
              el("span", { class: "sub" }, `${s.packages} συσκευασίες`),
            ),
          ),
        );
      }
      if (results.length) box.append(el("div", { class: "grp" }, "Συσκευασίες"));
    }
    if (!total && !subHits.length) {
      box.append(
        el(
          "div",
          { class: "none" },
          "Δεν βρέθηκε στα δελτία τιμών του Υπουργείου. Δοκιμάστε τη δραστική ουσία ή τον κωδικό ΕΟΦ.",
        ),
      );
    }
    for (const r of results) {
      const [brand, rest] = splitName(r.name);
      box.append(
        el(
          "a",
          { href: `/farmako/${r.eof_code}/`, role: "option", "aria-selected": "false" },
          el(
            "span",
            { class: "t" },
            el("span", { class: "nm" }, el("b", {}, brand), " ", el("span", {}, rest)),
            // the name is often cut off: strength and pack size first tell packs apart
            el("span", { class: "sub" }, [r.pack, r.substances.join(" + ")].filter(Boolean).join(" · ")),
          ),
          el("span", { class: "pr" }, eur(r.price_retail)),
        ),
      );
    }
    if (total > results.length) {
      box.append(
        el(
          "a",
          { href: `/anazitisi?q=${encodeURIComponent(q)}`, class: "all", role: "option", "aria-selected": "false" },
          `Όλα τα αποτελέσματα (${total})`,
        ),
      );
    }
    box.hidden = false;
    input.setAttribute("aria-expanded", "true");
    active = -1;
  }

  input.addEventListener("focus", () => loadIndex().catch(() => {}), { once: true });
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(render, 90);
  });
  input.addEventListener("keydown", (ev) => {
    const a = items();
    if (ev.key === "ArrowDown" && a.length) {
      ev.preventDefault();
      highlight((active + 1) % a.length);
    } else if (ev.key === "ArrowUp" && a.length) {
      ev.preventDefault();
      highlight((active - 1 + a.length) % a.length);
    } else if (ev.key === "Escape") {
      close();
    } else if (ev.key === "Enter" && active >= 0 && a[active]) {
      ev.preventDefault();
      location.href = a[active].href;
    }
  });
  form.addEventListener("submit", (ev) => {
    const code = codeFromQuery(input.value);
    if (code) {
      ev.preventDefault();
      location.href = `/farmako/${code}/`;
    }
  });
  document.addEventListener("click", (ev) => {
    if (!form.contains(ev.target)) close();
  });
}

// Packages of one substance by company: the same rows, regrouped under each
// company's name (A–Ω), in the order they had (strength, then price).
function companyGroups(groups) {
  const by = new Map();
  for (const li of groups.flatMap((g) => [...g.querySelectorAll("li[data-form]")])) {
    const k = li.dataset.mah || "—";
    if (!by.has(k)) by.set(k, []);
    by.get(k).push(li.cloneNode(true));
  }
  return [...by.keys()]
    .sort((a, b) => a.localeCompare(b, "el"))
    .map((k) =>
      el(
        "section",
        { class: "group mahg", hidden: "" },
        el("h2", {}, k, " ", el("span", {}, String(by.get(k).length))),
        el("ul", { class: "rows" }, ...by.get(k)),
      ),
    );
}

// Substance pages: filter by dose form, group by strength or company, sort by price or name.
function setupFilters(box) {
  const byStrength = [...document.querySelectorAll(".group")];
  const chips = [...box.querySelectorAll(".fchip")];
  const sort = box.querySelector("select[data-sort]");
  const grouping = box.querySelector("select[data-group]");
  let byCompany = null;
  let form = "all";
  const apply = () => {
    const company = grouping?.value === "mah";
    if (company && !byCompany) {
      byCompany = companyGroups(byStrength);
      byStrength.at(-1).after(...byCompany);
      if (sort && sort.value !== "price") order();
    }
    for (const [groups, on] of [[byStrength, !company], [byCompany || [], company]]) {
      for (const g of groups) {
        let shown = 0;
        for (const li of g.querySelectorAll("li[data-form]")) {
          li.hidden = form !== "all" && li.dataset.form !== form;
          if (!li.hidden) shown++;
        }
        g.hidden = !on || shown === 0;
      }
    }
  };
  const order = () => {
    const by = sort.value;
    for (const ul of document.querySelectorAll(".group .rows")) {
      const items = [...ul.children];
      items.sort((a, b) =>
        by === "name"
          ? a.dataset.name.localeCompare(b.dataset.name, "el")
          : Number(a.dataset.price) - Number(b.dataset.price) || a.dataset.name.localeCompare(b.dataset.name, "el"),
      );
      ul.append(...items);
    }
  };
  chips.forEach((c) =>
    c.addEventListener("click", () => {
      form = c.dataset.form;
      chips.forEach((x) => x.setAttribute("aria-pressed", String(x === c)));
      apply();
    }),
  );
  sort?.addEventListener("change", order);
  grouping?.addEventListener("change", apply);
  box.hidden = false;
}

document.querySelectorAll(".filters").forEach(setupFilters);

document.querySelectorAll("form.search").forEach((form) => {
  setup(form);
  initScan(form); // adds a camera button only where barcode scanning is supported
});

// "Install app" button: only where the browser offers installation (Chrome,
// Edge, Android). iPhone users get the hint on the About page instead.
let installEvent = null;
const appBox = document.querySelector(".foot-app");
window.addEventListener("beforeinstallprompt", (ev) => {
  ev.preventDefault();
  installEvent = ev;
  if (appBox) appBox.hidden = false;
});
appBox?.querySelector(".install")?.addEventListener("click", async () => {
  if (!installEvent) return;
  installEvent.prompt();
  await installEvent.userChoice.catch(() => null);
  installEvent = null;
  appBox.hidden = true;
});
window.addEventListener("appinstalled", () => {
  if (appBox) appBox.hidden = true;
});

// Print buttons (no inline handlers: the CSP allows only our own scripts)
document.querySelectorAll("[data-print]").forEach((b) => b.addEventListener("click", () => window.print()));

// Leaflet on the package page: a link to a section or heading inside a closed
// <details> opens it first (browsers without fragment auto-expand need this),
// and printing shows every section.
function openTarget(hash) {
  if (!hash || hash.length < 2) return;
  const t = document.getElementById(decodeURIComponent(hash.slice(1)));
  const d = t?.closest("details");
  if (d && !d.open) {
    d.open = true;
    t.scrollIntoView();
  }
}
openTarget(location.hash);
window.addEventListener("hashchange", () => openTarget(location.hash));
document.addEventListener("click", (ev) => {
  const a = ev.target.closest?.('a[href^="#"]');
  if (a) openTarget(a.getAttribute("href"));
});
window.addEventListener("beforeprint", () => {
  document.querySelectorAll("details.lf-sec").forEach((d) => (d.open = true));
});
