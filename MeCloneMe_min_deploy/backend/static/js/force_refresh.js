// mcm hotfix mcm-20250831-110605-utc
// Open /static/js/force_refresh.js directly to nuke caches & SW on this device.
(async () => {
  if ('serviceWorker' in navigator) {
    try {
      const regs = await navigator.serviceWorker.getRegistrations();
      for (const r of regs) try { await r.unregister(); } catch(_){}
    } catch(_) {}
  }
  if ('caches' in window) {
    const keys = await caches.keys();
    await Promise.all(keys.map(k => caches.delete(k)));
  }
  // Hard reload
  location.replace(location.pathname + '?bust=mcm-20250831-110605-utc');
})();
