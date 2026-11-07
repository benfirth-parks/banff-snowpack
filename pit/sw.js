// Offline support for the Snowpack Translator (/pit/). The app shell is cached at install; the page is served
// network-first so an update shows when online, and from the cache in the pit. Google Fonts are cached as they
// load; without them the page falls back to system fonts.
const VERSION = "pit-v1";
const SHELL = ["./", "./index.html", "./manifest.webmanifest", "./icon-192.png", "./icon-512.png",
  "./icon-maskable-512.png", "./apple-touch-icon.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k.startsWith("pit-") && k !== VERSION).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin === self.location.origin && url.pathname.startsWith(new URL("./", self.location).pathname)) {
    if (req.mode === "navigate") {
      e.respondWith(fetch(req).then((res) => {
        const copy = res.clone(); caches.open(VERSION).then((c) => c.put("./index.html", copy)); return res;
      }).catch(() => caches.match("./index.html")));
      return;
    }
    e.respondWith(caches.match(req).then((hit) => hit || fetch(req)));
    return;
  }
  if (url.hostname === "fonts.googleapis.com" || url.hostname === "fonts.gstatic.com") {
    e.respondWith(caches.open(VERSION + "-fonts").then((c) => c.match(req).then((hit) => hit || fetch(req).then((res) => {
      c.put(req, res.clone()); return res;
    }))));
  }
});
