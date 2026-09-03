/* App-shell caching only.
   Offline access to scores is deliberately out of scope, and page images are
   already immutable and cached by the ordinary HTTP cache -- putting them here
   too would be a second, worse cache with a storage quota to manage. */

const SHELL = "sms-shell-v2";
const ASSETS = [
  "/static/app.css",
  "/static/annotate.js",
  "/static/stayawake.js",
  "/manifest.webmanifest",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((cache) => cache.addAll(ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin) { return; }
  // Only the shell. Everything else goes to the network, so the catalogue is
  // never served stale.
  if (!ASSETS.includes(url.pathname)) { return; }

  event.respondWith(
    caches.match(event.request).then((hit) => hit || fetch(event.request))
  );
});
