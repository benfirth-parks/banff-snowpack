// The Snowpack Translator moved to https://snowpack-translator.netlify.app (2026-11-14). This worker replaces the old
// app's offline copy with the "moved" page, so a phone that installed /pit/ sees where to go, with or without signal.
const VERSION = "pit-moved";
const SHELL = ["./", "./index.html", "./voice.html", "./manifest.webmanifest", "./icon-192.png", "./apple-touch-icon.png"];
const plain = (res) => (res && res.redirected
  ? res.blob().then((body) => new Response(body, { status: res.status, statusText: res.statusText, headers: res.headers }))
  : Promise.resolve(res));

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSION)
    .then((c) => Promise.all(SHELL.map((u) => fetch(u, { cache: "reload" }).then(plain).then((res) => res && res.ok && c.put(u, res)))))
    .then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k.startsWith("pit-") && k !== VERSION).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET" || new URL(req.url).origin !== self.location.origin) return;
  e.respondWith(fetch(req).then(plain).catch(() => caches.match(req.mode === "navigate" ? "./index.html" : req, { ignoreSearch: true })
    .then((hit) => hit || Response.error())));
});
