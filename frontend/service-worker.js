/* El Fermentario — service worker.
   POLICY: NETWORK-FIRST for the app shell, NEVER cache the API.

   Why network-first (and not cache-first): during active development,
   cache-first serves yesterday's files until CACHE_VERSION is bumped — a
   fixed bug can look "back" just because the cache outlived the fix. With
   network-first the browser always tries the server (fresh files whenever
   online) and falls back to the last good cached copy only when offline.
   The cost is a slightly slower open; the gain is that what you see is
   what is on disk. Revisit cache-first when the app is hosted and stable.

   API requests (anything not in SHELL_FILES) always pass straight through:
   stock numbers must come from the server — a cached /inventory would be
   the web equivalent of violating "memory always matches disk". */

const CACHE_VERSION = "fermentario-shell-v3";
const SHELL_FILES = [
  "./",
  "index.html",
  "styles.css",
  "app.js",
  "manifest.webmanifest",
  "icons/icon-192.png",
  "icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_VERSION).then((cache) => cache.addAll(SHELL_FILES)),
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_VERSION)
                      .map((k) => caches.delete(k)))),
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  const is_shell = url.origin === self.location.origin &&
    SHELL_FILES.some((f) => url.pathname.endsWith("/" + f.replace("./", "")) ||
                            url.pathname === "/");
  if (event.request.method !== "GET" || !is_shell) {
    return;   // API and everything else: straight to the network, uncached
  }
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        // Keep the cache warm with the freshest copy for offline fallback.
        const copy = response.clone();
        caches.open(CACHE_VERSION).then((cache) => cache.put(event.request, copy));
        return response;
      })
      .catch(() => caches.match(event.request)),   // offline: last good copy
  );
});
