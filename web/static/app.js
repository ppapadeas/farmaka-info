// Instant search suggestions. Without JavaScript the forms still work: they
// submit to /anazitisi, rendered by the worker.
import { codeFromQuery, eur, parseIndex, search } from "./search-core.js";

let indexPromise = null;
function loadIndex() {
  if (!indexPromise) {
    indexPromise = fetch("/search-index.tsv")
      .then((r) => {
        if (!r.ok) throw new Error(`search index: ${r.status}`);
        return r.text();
      })
      .then(parseIndex)
      .catch((err) => {
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
    let rows;
    try {
      rows = await loadIndex();
    } catch {
      return close();
    }
    if (input.value.trim() !== q) return;
    const { total, results } = search(rows, q, 8);
    box.replaceChildren();
    if (!total) {
      box.append(el("div", { class: "none" }, "Δεν βρέθηκε συσκευασία."));
    }
    for (const r of results) {
      box.append(
        el(
          "a",
          { href: `/farmako/${r.eof_code}/`, role: "option", "aria-selected": "false" },
          el("span", { class: "pr" }, eur(r.price_retail)),
          el("span", { class: "nm" }, r.name),
          el("span", { class: "sub" }, [r.substances.join(" + "), r.mah].filter(Boolean).join(" · ")),
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

document.querySelectorAll("form.search").forEach(setup);
