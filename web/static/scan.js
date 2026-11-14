// Barcode scanning from the search forms, with the browser's own BarcodeDetector.
// Frames stay on the device: they are read by the detector and never stored or sent.
// Without BarcodeDetector or a camera API nothing is added to the page.
import { codeFromQuery } from "./search-core.js";

export const HINT = "Στρέψτε την κάμερα στο barcode της συσκευασίας";
export const NOT_MEDICINE = "Δεν είναι κωδικός φαρμάκου";

const SCAN_INTERVAL_MS = 160; // about 6 frames per second
const WARN_MS = 2000;
const SVG_NS = "http://www.w3.org/2000/svg";

// --- pure helpers (tested in web/test/scan.test.mjs) ---

export function isSupported(win = globalThis.window) {
  return Boolean(win && "BarcodeDetector" in win && win.navigator?.mediaDevices?.getUserMedia);
}

export function ean13Valid(digits) {
  if (!/^\d{13}$/.test(digits)) return false;
  let sum = 0;
  for (let i = 0; i < 12; i++) sum += Number(digits[i]) * (i % 2 ? 3 : 1);
  return (10 - (sum % 10)) % 10 === Number(digits[12]);
}

// From one frame's detections: the first Greek medicine code, and whether a
// valid EAN-13 that is not a medicine code was seen.
export function pickCode(barcodes) {
  let foreign = false;
  for (const b of barcodes ?? []) {
    if (b?.format && b.format !== "ean_13") continue;
    const raw = String(b?.rawValue ?? "").trim();
    if (!ean13Valid(raw)) continue;
    const code = codeFromQuery(raw);
    if (code) return { code, foreign: false };
    foreign = true;
  }
  return { code: null, foreign };
}

export function packageUrl(code) {
  return `/farmako/${code}/`;
}

export function cameraError(name) {
  switch (name) {
    case "NotAllowedError":
    case "SecurityError":
      return "Δεν δόθηκε άδεια για την κάμερα. Μπορείτε να την επιτρέψετε από τις ρυθμίσεις του browser.";
    case "NotFoundError":
    case "OverconstrainedError":
      return "Δεν βρέθηκε κάμερα σε αυτή τη συσκευή.";
    case "NotReadableError":
    case "AbortError":
      return "Η κάμερα χρησιμοποιείται από άλλη εφαρμογή.";
    default:
      return "Η κάμερα δεν ξεκίνησε.";
  }
}

// --- DOM ---

function icon(...paths) {
  const s = document.createElementNS(SVG_NS, "svg");
  for (const [k, v] of Object.entries({
    viewBox: "0 0 24 24", width: "22", height: "22", fill: "none", stroke: "currentColor",
    "stroke-width": "1.8", "stroke-linecap": "round", "stroke-linejoin": "round",
    "aria-hidden": "true", focusable: "false",
  })) s.setAttribute(k, v);
  for (const d of paths) {
    const p = document.createElementNS(SVG_NS, "path");
    p.setAttribute("d", d);
    s.append(p);
  }
  return s;
}

const BARCODE_ICON = [
  "M3 8V5a2 2 0 0 1 2-2h3", "M16 3h3a2 2 0 0 1 2 2v3",
  "M21 16v3a2 2 0 0 1-2 2h-3", "M8 21H5a2 2 0 0 1-2-2v-3",
  "M7 8v8", "M10 8v8", "M13 8v8", "M17 8v8",
];
const CLOSE_ICON = ["M6 6l12 12", "M18 6L6 18"];

let supportPromise = null;
function checkSupport() {
  if (!supportPromise) {
    supportPromise = (async () => {
      if (!isSupported()) return false;
      try {
        const BD = globalThis.window.BarcodeDetector;
        if (typeof BD.getSupportedFormats !== "function") return true;
        return (await BD.getSupportedFormats()).includes("ean_13");
      } catch {
        return false;
      }
    })();
  }
  return supportPromise;
}

// One dialog per page, shared by all search forms.
let ui = null;
let detector = null;
let stream = null;
let session = 0;
let timer = 0;
let warnTimer = 0;

function stopCamera() {
  session++;
  clearTimeout(timer);
  clearTimeout(warnTimer);
  if (stream) for (const t of stream.getTracks()) t.stop();
  stream = null;
  if (ui) {
    ui.video.pause();
    ui.video.srcObject = null;
  }
}

// Stop the camera first: dialog.close() fires its close event asynchronously.
function closeScanner() {
  stopCamera();
  if (ui?.dialog.open) ui.dialog.close();
}

function setMsg(text, warn = false) {
  ui.msg.textContent = text;
  ui.msg.classList.toggle("is-warn", warn);
}

function buildDialog() {
  const dialog = document.createElement("dialog");
  dialog.className = "scan-dialog";
  dialog.setAttribute("aria-label", "Σάρωση barcode");

  const view = document.createElement("div");
  view.className = "scan-view";
  const video = document.createElement("video");
  video.muted = true;
  video.playsInline = true;
  video.setAttribute("playsinline", "");
  video.setAttribute("muted", "");
  video.setAttribute("aria-hidden", "true");
  const guide = document.createElement("div");
  guide.className = "scan-guide";
  guide.setAttribute("aria-hidden", "true");
  view.append(video, guide);

  const msg = document.createElement("p");
  msg.className = "scan-msg";
  msg.setAttribute("role", "status");
  msg.setAttribute("aria-live", "polite");

  const close = document.createElement("button");
  close.type = "button";
  close.className = "scan-close";
  close.setAttribute("aria-label", "Κλείσιμο");
  close.append(icon(...CLOSE_ICON));
  close.addEventListener("click", closeScanner);

  dialog.append(view, msg, close);
  // Escape closes the dialog natively; every way out ends in stopCamera.
  dialog.addEventListener("cancel", stopCamera);
  dialog.addEventListener("close", stopCamera);
  dialog.addEventListener("click", (ev) => {
    if (ev.target === dialog) closeScanner(); // backdrop
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) closeScanner();
  });
  window.addEventListener("pagehide", closeScanner);

  document.body.append(dialog);
  return { dialog, video, msg };
}

function scanLoop(mine) {
  if (mine !== session) return;
  const next = () => {
    if (mine === session) timer = setTimeout(() => scanLoop(mine), SCAN_INTERVAL_MS);
  };
  if (ui.video.readyState < 2) return next(); // no frame yet
  detector
    .detect(ui.video)
    .then((found) => {
      if (mine !== session) return;
      const { code, foreign } = pickCode(found);
      if (code) {
        closeScanner();
        location.assign(packageUrl(code));
        return;
      }
      if (foreign) {
        setMsg(NOT_MEDICINE, true);
        clearTimeout(warnTimer);
        warnTimer = setTimeout(() => setMsg(HINT), WARN_MS);
      }
      next();
    })
    .catch(next);
}

async function openScanner() {
  ui ??= buildDialog();
  if (ui.dialog.open) return;
  stopCamera();
  const mine = session;
  setMsg(HINT);
  ui.dialog.showModal();
  let s;
  try {
    detector ??= new window.BarcodeDetector({ formats: ["ean_13"] });
    s = await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } },
    });
  } catch (err) {
    if (mine === session) setMsg(cameraError(err?.name), true);
    return;
  }
  if (mine !== session || !ui.dialog.open) {
    // closed while the permission prompt was up
    for (const t of s.getTracks()) t.stop();
    return;
  }
  stream = s;
  ui.video.srcObject = s;
  try {
    await ui.video.play();
  } catch {
    // autoplay of a muted inline video is allowed; frames still arrive if this rejects
  }
  scanLoop(mine);
}

export async function initScan(form) {
  if (!(await checkSupport())) return false;
  const input = form?.querySelector?.("input[name=q]");
  if (!input || form.querySelector(".scan-btn")) return false;
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "scan-btn";
  btn.setAttribute("aria-label", "Σάρωση barcode");
  btn.title = "Σάρωση barcode";
  btn.append(icon(...BARCODE_ICON));
  btn.addEventListener("click", openScanner);
  input.after(btn);
  return true;
}
