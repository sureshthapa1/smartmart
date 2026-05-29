// Smart Mart Service Worker v2 — PWA install + offline support
const CACHE = 'smartmart-v2';
const PRECACHE_URLS = [
  '/auth/login',
  '/static/manifest.json',
];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(PRECACHE_URLS)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// Network first, cache fallback, offline page for navigation requests
self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;

  // For navigation (HTML pages), try network then show cached login page
  if (e.request.mode === 'navigate') {
    e.respondWith(
      fetch(e.request).catch(() =>
        caches.match('/auth/login').then(r =>
          r || new Response('<h2>You are offline. Please reconnect.</h2>', {
            headers: { 'Content-Type': 'text/html' }
          })
        )
      )
    );
    return;
  }

  // For static assets: cache first (fast), revalidate in background
  if (e.request.url.includes('/static/')) {
    e.respondWith(
      caches.open(CACHE).then(cache =>
        cache.match(e.request).then(cached => {
          const fetchPromise = fetch(e.request).then(response => {
            if (response.ok) cache.put(e.request, response.clone());
            return response;
          }).catch(() => cached);
          return cached || fetchPromise;
        })
      )
    );
    return;
  }

  // All other GET requests: network first
  e.respondWith(
    fetch(e.request).catch(() => caches.match(e.request))
  );
});
