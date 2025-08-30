/* MeCloneMe PWA Service Worker
   Strategy:
   - network-first for HTML/CSS/JS (always try fresh on deploy)
   - stale-while-revalidate for images/fonts
   - versionless: updates happen because we do network-first
*/

const CACHE_NAME_STATIC = 'mcm-static-v1';
const CACHE_NAME_IMG = 'mcm-img-v1';

const HTML_JS_CSS = /\.(html?|js|css)(\?|$)/i;
const IMG_FONT = /\.(png|jpg|jpeg|gif|svg|webp|ico|ttf|woff2?|eot)(\?|$)/i;

self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keep = new Set([CACHE_NAME_STATIC, CACHE_NAME_IMG]);
    const keys = await caches.keys();
    await Promise.all(keys.map(k => !keep.has(k) && caches.delete(k)));
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  const url = new URL(req.url);

  // Bypass non-GET and other origins
  if (req.method !== 'GET' || url.origin !== location.origin) return;

  if (HTML_JS_CSS.test(url.pathname)) {
    // Network-first
    event.respondWith((async () => {
      try {
        const fresh = await fetch(req, { cache: 'no-store' });
        const cache = await caches.open(CACHE_NAME_STATIC);
        cache.put(req, fresh.clone());
        return fresh;
      } catch (e) {
        const cache = await caches.open(CACHE_NAME_STATIC);
        const cached = await cache.match(req);
        return cached || new Response('Offline', {status: 503});
      }
    })());
    return;
  }

  if (IMG_FONT.test(url.pathname)) {
    // Stale-while-revalidate
    event.respondWith((async () => {
      const cache = await caches.open(CACHE_NAME_IMG);
      const cached = await cache.match(req);
      const fetchPromise = fetch(req).then((resp) => {
        cache.put(req, resp.clone());
        return resp;
      }).catch(() => null);
      return cached || fetchPromise;
    })());
    return;
  }
});
