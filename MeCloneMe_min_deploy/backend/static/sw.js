// mcm hotfix mcm-20250831-110605-utc
const CACHE_NAME = 'mcm-cache-mcm-20250831-110605-utc';
const CORE_HTML = ['/', '/start', '/onboarding', '/onboarding_mobile', '/index', '/mobile'];

self.addEventListener('install', (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(['/'])) // prime minimally
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.map(k => k !== CACHE_NAME ? caches.delete(k) : Promise.resolve()));
    await self.clients.claim();
  })());
});

// Prefer network for HTML routes (so new templates/css load), cache-first for static assets.
self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);

  const isHTML = req.headers.get('accept')?.includes('text/html') ||
                 CORE_HTML.some(p => url.pathname === p || url.pathname.startsWith(p + '/'));

  if (isHTML) {
    event.respondWith(
      fetch(req).then(r => {
        const copy = r.clone();
        caches.open(CACHE_NAME).then(c => c.put(req, copy));
        return r;
      }).catch(() => caches.match(req))
    );
    return;
  }

  // Static: cache-first
  event.respondWith(
    caches.match(req).then(m => m || fetch(req).then(r => {
      const copy = r.clone();
      caches.open(CACHE_NAME).then(c => c.put(req, copy));
      return r;
    }))
  );
});

self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') self.skipWaiting();
});
