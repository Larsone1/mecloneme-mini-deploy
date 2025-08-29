// Tiny cache-then-network SW (scope limited under /static/js/)
const CACHE = 'mcm-pwa-v1';
const CORE = [
  '/static/css/mc_next.css',
  '/static/css/mc_pwa.css',
  '/static/js/mc_next.js',
  '/static/js/pwa.js',
  '/static/img/logo_mecloneme.png'
];
self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(CORE)));
});
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k)))));
});
self.addEventListener('fetch', (e) => {
  e.respondWith(fetch(e.request).catch(()=>caches.match(e.request)));
});