// Offline support for the Snowpack Translator (/pit/): the photo page, the voice page and their scripts.
// Everything in SHELL is cached at install. Pages and scripts are fetched fresh when there is a connection (so an
// update shows at once) and come from the cache when there is none, or when the network takes more than 3 seconds,
// as it does on one bar in the mountains. Google Fonts are cached as they load; without them the pages fall back to
// system fonts. Bump VERSION whenever a file in SHELL changes.
const VERSION = "pit-v3";
const SHELL = ["./", "./index.html", "./voice.html", "./js/parse.js", "./js/model.js", "./js/draw.js", "./js/voice.js",
  "./manifest.webmanifest", "./icon-192.png", "./icon-512.png", "./icon-maskable-512.png", "./apple-touch-icon.png"];

// A host with "pretty URLs" (Netlify) answers voice.html with a redirect to /voice. A redirected response cannot
// answer a page load, so every response is stored as a plain copy.
const plain = (res) => (res && res.redirected
  ? res.blob().then((body) => new Response(body, { status: res.status, statusText: res.statusText, headers: res.headers }))
  : Promise.resolve(res));

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSION)
    .then((c) => Promise.all(SHELL.map((u) => fetch(u, { cache: "reload" }).then(plain).then((res) => {
      if (!res || !res.ok) throw new Error("could not cache " + u);
      return c.put(u, res);
    }))))
    .then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k.startsWith("pit-") && k !== VERSION && k !== VERSION + "-fonts").map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

// Network first with a time limit, then the cached copy stored under `key`.
function fresh(req, key) {
  const cached = () => caches.match(key, { ignoreSearch: true });
  return new Promise((resolve) => {
    let done = false;
    const timer = setTimeout(() => cached().then((hit) => { if (hit && !done) { done = true; resolve(hit); } }), 3000);
    fetch(req).then((res) => {
      clearTimeout(timer);
      if (res && res.ok) plain(res.clone()).then((copy) => caches.open(VERSION).then((c) => c.put(key, copy)));
      if (!done) { done = true; resolve(res); }
    }).catch(() => { clearTimeout(timer); cached().then((hit) => { if (!done) { done = true; resolve(hit || Response.error()); } }); });
  });
}

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url), scope = new URL("./", self.location).pathname;
  if (url.origin === self.location.origin && url.pathname.startsWith(scope)) {
    const rel = "./" + url.pathname.slice(scope.length);
    if (req.mode === "navigate") { e.respondWith(fresh(req, /voice(\.html)?$/.test(rel) ? "./voice.html" : "./index.html")); return; }
    e.respondWith(fresh(req, rel));
    return;
  }
  if (url.hostname === "fonts.googleapis.com" || url.hostname === "fonts.gstatic.com") {
    e.respondWith(caches.open(VERSION + "-fonts").then((c) => c.match(req).then((hit) => hit || fetch(req).then((res) => {
      c.put(req, res.clone()); return res;
    }))));
  }
});
