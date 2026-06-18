const CACHE_NAME = "tek2day-finance-v12";
const APP_SHELL = [
  "/",
  "/static/favicon.ico",
  "/static/tek2day-icon.png",
  "/static/icon-512.png",
  "/static/apple-touch-icon.png",
  "/manifest.webmanifest"
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  // Only handle our own app/static requests. Let the browser handle everything else
  // directly: cross-origin (Google Fonts/gstatic), the Firebase auth proxy (/__/*),
  // and the dynamic API (/api/*) must NOT be intercepted or cached by the SW.
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/__/") || url.pathname.startsWith("/api/")) return;
  if (event.request.mode === "navigate") {
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
          return response;
        })
        .catch(() => caches.match(event.request))
    );
    return;
  }
  event.respondWith(
    caches.match(event.request).then((cached) => cached || fetch(event.request))
  );
});
