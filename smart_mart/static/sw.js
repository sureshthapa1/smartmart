// Smart Mart Service Worker v3 — PWA install + offline support
// Bump cache name whenever precache URLs change to force update.
const CACHE = 'smartmart-v5';
const PRECACHE_URLS = [
  '/store/',
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

// Network first, cache fallback
self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;

  // For navigation (HTML pages), try network then serve cached store home
  if (e.request.mode === 'navigate') {
    e.respondWith(
      fetch(e.request).catch(() =>
        caches.match('/store/').then(r =>
          r || new Response(
            '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Offline — GoldKernel</title><style>body{font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;background:#fffbf5;color:#1c1917;text-align:center;padding:2rem}.ico{font-size:4rem;margin-bottom:1rem}h1{font-size:1.5rem;margin:.5rem 0}p{color:#78716c}</style></head><body><div><div class="ico">📦</div><h1>You are offline</h1><p>Please check your internet connection and try again.</p></div></body></html>',
            { headers: { 'Content-Type': 'text/html' } }
          )
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
