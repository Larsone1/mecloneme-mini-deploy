// MeCloneMe PWA Service Worker
const VERSION = '2025-08-30-2130';
const CACHE_NAME = `mcm-cache-${VERSION}`;
const ASSETS = [
  '/',
  '/onboarding',
  '/static/css/mc_p10.css?v=' + VERSION,
  '/static/js/onboarding.js?v=' + VERSION,
  '/static/img/logo_mecloneme.png?v=' + VERSION
];

self.addEventListener('install', (event) => {
  self.skipWaiting();
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(ASSETS)));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(k => k.startsWith('mcm-cache-') && k !== CACHE_NAME).map(k => caches.delete(k))))
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  event.respondWith((async () => {
    try {
      const resp = await fetch(req);
      const copy = resp.clone();
      const cache = await caches.open(CACHE_NAME);
      cache.put(req, copy);
      return resp;
    } catch (err) {
      const cached = await caches.match(req, { ignoreSearch: true });
      if (cached) return cached;
      throw err;
    }
  })());
});
