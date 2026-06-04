/**
 * Shekel Budget App -- Service Worker
 *
 * Static-asset-only cache.  Speeds repeat loads and unlocks PWA
 * installability ("Add to Home Screen") without ever caching HTML
 * or JSON.  Caching dynamic responses would let the app serve
 * yesterday's balances offline, which the financial-correctness
 * audit explicitly forbids -- see implementation_plan_mobile_v3.md
 * Section 0 "Consequence of getting this wrong" point 2, and
 * D-I in Section 2 for the cache-scope decision.
 *
 * Invariant: the fetch handler returns `respondWith` ONLY for
 * URL prefixes in `STATIC_PREFIXES` below.  For everything else
 * (HTML, JSON, HTMX partials, mutating verbs, cross-origin) the
 * handler returns without intercepting, so the browser performs
 * a normal network fetch.  A user offline sees an honest
 * connection error for app pages, not a stale projection.
 */

// IMPORTANT: bump this version string whenever a cached /static asset
// (CSS, JS, fonts, images) changes.  This worker is cache-first for the
// STATIC_PREFIXES below, so without a bump a returning user keeps being
// served the old file from Cache Storage; the activate handler evicts
// the prior version on the next load.  v2: htmx-indicator rules added to
// app.css (the CSP spinner fix) and the grid pay-period-change handlers
// added to app.js.
const CACHE = 'shekel-static-v2';
const STATIC_PREFIXES = [
  '/static/vendor/',
  '/static/css/',
  '/static/js/',
  '/static/img/',
  '/static/fonts/',
  '/static/manifest.json',
];

self.addEventListener('install', function () {
  // Activate the new worker immediately; the cache is populated
  // lazily as static fetches arrive, so there is no pre-cache list
  // to await here.
  self.skipWaiting();
});

self.addEventListener('activate', function (event) {
  // Purge any previous-version static caches so a bumped CACHE
  // name (e.g. shekel-static-v2) cleanly evicts v1 on the next
  // activation.  Non-matching cache names are left alone in case
  // a future feature uses a separate cache namespace.
  event.waitUntil(
    caches.keys().then(function (names) {
      return Promise.all(names
        .filter(function (n) {
          return n.startsWith('shekel-static-') && n !== CACHE;
        })
        .map(function (n) { return caches.delete(n); })
      );
    }).then(function () { return self.clients.claim(); })
  );
});

self.addEventListener('fetch', function (event) {
  // Mutating verbs always pass through -- the browser will not
  // even try cache lookups for non-GET, but be explicit.
  if (event.request.method !== 'GET') return;

  var url = new URL(event.request.url);

  // Cross-origin (CDN, analytics, anything not served by this app)
  // passes through.  The cache namespace is for this origin only.
  if (url.origin !== self.location.origin) return;

  var isStatic = STATIC_PREFIXES.some(function (prefix) {
    return url.pathname.startsWith(prefix);
  });

  // CRITICAL: no respondWith for non-static requests.  HTML pages,
  // JSON endpoints, HTMX partials, the /grid view, /dashboard,
  // /companion -- all flow straight to network.  See the
  // financial-correctness invariant at the top of this file.
  if (!isStatic) return;

  event.respondWith(
    caches.open(CACHE).then(function (cache) {
      return cache.match(event.request).then(function (cached) {
        if (cached) return cached;
        return fetch(event.request).then(function (response) {
          // Only persist successful responses; a 404 or 5xx must
          // not be promoted into cache, where it would mask the
          // recovered asset on the next reload.
          if (response.ok) cache.put(event.request, response.clone());
          return response;
        });
      });
    })
  );
});
