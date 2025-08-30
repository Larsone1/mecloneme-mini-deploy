// Root Service Worker for MeCloneMe
const CACHE_STATIC = 'mcm-static-v1';
const CACHE_IMG = 'mcm-img-v1';

self.addEventListener('install', e => { self.skipWaiting(); });
self.addEventListener('activate', e => { e.waitUntil((async () => {
  const keep = new Set([CACHE_STATIC, CACHE_IMG]);
  const keys = await caches.keys();
  await Promise.all(keys.map(k => !keep.has(k) && caches.delete(k)));
  await self.clients.claim();
})()); });

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;

  if (url.pathname.match(/\.(html?|js|css)(\?|$)/i)) {
    event.respondWith((async () => {
      try {
        const fresh = await fetch(req, { cache: 'no-store' });
        const cache = await caches.open(CACHE_STATIC);
        cache.put(req, fresh.clone());
        return fresh;
      } catch (e) {
        const cache = await caches.open(CACHE_STATIC);
        const cached = await cache.match(req);
        return cached || new Response('Offline', {{status: 503}});
      }
    })());
    return;
  }

  if (url.pathname.match(/\.(png|jpg|jpeg|gif|svg|webp|ico|ttf|woff2?|eot)(\?|$)/i)) {
    event.respondWith((async () => {
      const cache = await caches.open(CACHE_IMG);
      const cached = await cache.match(req);
      const fetchPromise = fetch(req).then(resp => {{ cache.put(req, resp.clone()); return resp; }}).catch(() => null);
      return cached || fetchPromise;
    })());
    return;
  }
});
