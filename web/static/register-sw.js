// Registers the offline service worker (served at /sw.js). The site works the
// same without it, so any failure is ignored.
export function registerServiceWorker(nav = globalThis.navigator) {
  if (!nav || !("serviceWorker" in nav)) return false;
  const go = () => {
    try {
      nav.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
    } catch {
      // e.g. storage blocked
    }
  };
  // Wait for the page itself to load before the worker downloads its files.
  if (document.readyState === "complete") go();
  else window.addEventListener("load", go, { once: true });
  return true;
}

registerServiceWorker();
