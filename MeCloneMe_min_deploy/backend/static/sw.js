const VERSION='mcm-20250831-1320';
self.addEventListener('install', e=>{ self.skipWaiting(); });
self.addEventListener('activate', e=>{
  e.waitUntil((async()=>{
    const keys=await caches.keys();
    await Promise.all(keys.filter(k=>k!==VERSION).map(k=>caches.delete(k)));
    await self.clients.claim();
  })());
});
self.addEventListener('fetch', e=>{
  const req=e.request;
  if(req.method!=='GET') return;
  e.respondWith((async()=>{
    const cache=await caches.open(VERSION);
    const cached=await cache.match(req);
    if(cached) return cached;
    try{
      const res=await fetch(req);
      const url=new URL(req.url);
      const same=url.origin===self.location.origin;
      if(same && (url.pathname.startsWith('/static/') || ['style','script','image','font'].includes(req.destination))){
        cache.put(req, res.clone());
      }
      return res;
    }catch(_){ return cached || Response.error(); }
  })());
});