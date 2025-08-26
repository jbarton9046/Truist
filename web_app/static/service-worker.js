// static/service-worker.js
const CACHE = 'bt-v2'; // bump to invalidate older caches

const PRECACHE = [
  '/static/manifest.webmanifest',
  '/static/icons/icon-192.png',
  '/static/icons/icon-192-maskable.png',
  '/static/icons/icon-512.png',
  '/static/icons/icon-512-maskable.png',
  '/static/icons/favicon-16.png',
  '/static/icons/favicon-32.png',
  // Optionally add critical CSS/JS if you want them offline:
  // '/static/css/main.css',
  // '/static/js/drawer.js'
];

self.addEventListener('install', (event) => {
  // Activate this SW immediately after install
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(PRECACHE))
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    // Clean old caches
    const keys = await caches.keys();
    await Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)));
    // Speed up navigations if supported
    if (self.registration.navigationPreload) {
      try { await self.registration.navigationPreload.enable(); } catch(_) {}
    }
    // Take control of currently open clients
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (event) => {
  const req = event.request;

  // Only handle GETs
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  const sameOrigin = (url.origin === location.origin);

  // 1) HTML navigations: network-first, fallback to cache, then tiny offline page
  if (req.mode === 'navigate') {
    event.respondWith((async () => {
      try {
        // Use navigation preload if available
        const preloaded = await event.preloadResponse;
        if (preloaded) return preloaded;

        const net = await fetch(req);
        // Cache a copy of successful navigations (best-effort)
        caches.open(CACHE).then(c => c.put(req, net.clone()));
        return net;
      } catch {
        // Try a cached copy of this page
        const cached = await caches.match(req);
        if (cached) return cached;
        // Try cached root as a last resort
        const root = await caches.match('/');
        if (root) return root;
        // Minimal offline fallback
        return new Response(
          '<!doctype html><meta charset="utf-8"><title>Offline</title>' +
          '<body><h1>Offline</h1><p>You appear to be offline. Retry when you’re back online.</p></body>',
          { headers: { 'Content-Type': 'text/html; charset=utf-8' } }
        );
      }
    })());
    return;
  }

  // 2) Static assets (same-origin): cache-first
  const isStatic = sameOrigin && (
    ['image', 'style', 'script', 'font'].includes(req.destination) ||
    url.pathname.endsWith('.webmanifest') ||
    url.pathname.endsWith('.css') ||
    url.pathname.endsWith('.js') ||
    url.pathname.endsWith('.png') ||
    url.pathname.endsWith('.svg') ||
    url.pathname.endsWith('.ico') ||
    url.pathname.endsWith('.woff2')
  );

  if (isStatic) {
    event.respondWith((async () => {
      const hit = await caches.match(req);
      if (hit) return hit;

      const resp = await fetch(req);
      // Cache a copy (best-effort; ignore opaque/cors issues)
      caches.open(CACHE).then(c => c.put(req, resp.clone())).catch(() => {});
      return resp;
    })());
  }
});
