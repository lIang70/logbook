/* Service worker for the logbook site.
 *
 * Served from the site root so its default scope covers every page: GitHub
 * Pages cannot send `Service-Worker-Allowed`, so a copy under /assets/ would
 * only ever control /assets/.
 *
 * Strategies:
 *   HTML   network-first  — a fresh page must never be paired with stale
 *                           assets, and `navigation.instant` swaps HTML mid
 *                           session, so cached HTML on the first paint reads
 *                           as "the site is not updating".
 *   assets stale-while-revalidate — Material's bundle filenames carry a
 *                           content hash, so a stale asset can only ever be
 *                           requested by a stale page, which is also stale.
 *                           Our own files do NOT carry a hash, so they are
 *                           listed in VOLATILE and treated like HTML.
 *
 * `__BUILD_ID__` is stamped by web/build.py on every stage: a content-only
 * deploy still rotates the cache name, so readers never sit on an old build.
 */

const BUILD = "__BUILD_ID__";
const CACHE = `logbook-${BUILD}`;

/* Resolve against this script's own URL: the site may live under a subpath
 * (/logbook/), and hardcoding that would break local `make serve`. */
const ROOT = new URL("./", self.location).href;
const SHELL = [
  "",
  "manifest.json",
  "search.json",
  "assets/favicon.svg",
  "assets/icon-192.png",
  "assets/icon-512.png",
  "assets/apple-touch-icon.png",
  "assets/pwa.js",
].map((path) => ROOT + path);

/* Stable filenames that we own and edit often. Serving these stale pairs a
 * fresh page with the previous build's stylesheet, which reads as "the site is
 * broken" — the one thing stale-while-revalidate must not do here. */
const VOLATILE = ["stylesheets/theme.css"].map((path) => ROOT + path);

self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE);
      /* `cache: reload` skips the HTTP cache, otherwise a reinstall can
       * precache the previous build's bodies under the new cache name.
       * Individual adds instead of addAll(): addAll is all-or-nothing, so one
       * renamed generated file (search.json belongs to Zensical) would leave
       * the site with no worker at all. */
      await Promise.allSettled(
        SHELL.map((url) => cache.add(new Request(url, { cache: "reload" }))),
      );
    })(),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(
        keys.filter((key) => key !== CACHE).map((key) => caches.delete(key)),
      );
      await self.clients.claim();
    })(),
  );
});

self.addEventListener("message", (event) => {
  if (event.data === "skip-waiting") self.skipWaiting();
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  /* `navigation.instant` requests pages with fetch() (mode "cors"), so a
   * request is HTML if it is a navigation *or* it advertises text/html. */
  const wantsFresh =
    request.mode === "navigate" ||
    (request.headers.get("accept") || "").includes("text/html") ||
    VOLATILE.includes(url.href);

  event.respondWith(
    wantsFresh ? networkFirst(request) : staleWhileRevalidate(request),
  );
});

async function networkFirst(request) {
  const cache = await caches.open(CACHE);
  try {
    const fresh = await fetch(request);
    if (fresh.ok) await cache.put(request, fresh.clone());
    return fresh;
  } catch (error) {
    const cached = await cache.match(request, { ignoreSearch: true });
    if (cached) return cached;
    const root = await cache.match(ROOT);
    if (root) return root;
    throw error;
  }
}

async function staleWhileRevalidate(request) {
  const cache = await caches.open(CACHE);
  const cached = await cache.match(request);
  const network = fetch(request)
    .then(async (response) => {
      if (response.ok) await cache.put(request, response.clone());
      return response;
    })
    .catch(() => cached);
  return cached || network;
}
