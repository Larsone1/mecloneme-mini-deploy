const CACHE = 'mcm-v-25083101';
self.addEventListener('install', e => {
  self.skipWaiting();
  e.waitUntil(caches.open(CACHE));
});
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k)))) 
    .then(()=> self.clients.claim())
  );
});
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.mode === 'navigate' || (e.request.method === 'GET' && url.pathname.endsWith('.html'))) {
    return;
  }
  e.respondWith(
    caches.open(CACHE).then(cache => 
      cache.match(e.request).then(res => res || fetch(e.request).then(r => {
        if(r.ok && (url.pathname.startsWith('/static/'))) cache.put(e.request, r.clone());
        return r;
      }))
    )
  );
});
