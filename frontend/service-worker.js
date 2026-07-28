/* El Fermentario — service worker.
   POLICY: cache the app SHELL (html/css/js/icons), NEVER the API.
   Stock numbers must always come from the server: a cached /inventory or a
   replayed POST would show (or create) stale data — the web equivalent of
   violating the "memory always matches disk" invariant. So API requests
   (anything that is not a shell file) pass straight through to the network.

   Bump CACHE_VERSION whenever a shell file changes: activation then deletes
   the old cache, which is how updates reach installed PWAs. */

const CACHE_VERSION = "fermentario-shell-v2";
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
  // Shell: cache-first so the app opens instantly (and offline shows the UI,
  // which will then report "sin conexión" honestly instead of stale numbers).
  event.respondWith(
    caches.match(event.request).then((hit) => hit || fetch(event.request)),
  );
});
