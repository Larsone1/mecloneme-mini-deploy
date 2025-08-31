// Minimal SW: single-version cache, aggressive update/claim
const CACHE = 'mcm-25083104';

self.addEventListener('install', (e) => {
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))) 
    .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  // Network-first for HTML, cache-first for static
  if (req.headers.get('accept') && req.headers.get('accept').includes('text/html')) {
    e.respondWith(fetch(req).catch(() => caches.match(req)));
  } else {
    e.respondWith(
      caches.open(CACHE).then(cache =>
        cache.match(req).then(hit => hit || fetch(req).then(res => {
          // Only cache ok GET responses
          if (req.method === 'GET' && res.status === 200) {
            cache.put(req, res.clone());
          }
          return res;
        }))
      )
    );
  }
});
