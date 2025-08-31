// mcm cache/ServiceWorker hotfix (gated)
// Use by visiting your app URL with ?nuke=1 OR by opening /static/diag/index.html and pressing the button.
// Safe to include globally – it does NOTHING unless ?nuke=1 is present on the current page.
(() => {
  try {
    const url = new URL(location.href);
    const shouldNuke = url.searchParams.has('nuke');
    if (!shouldNuke) {
      // No-op in normal navigation
      return;
    }
    (async () => {
      try {
        if ('serviceWorker' in navigator) {
          try {
            const regs = await navigator.serviceWorker.getRegistrations();
            await Promise.all(regs.map(r => r.unregister().catch(() => {})));
          } catch (_) {}
        }
        if ('caches' in window) {
          try {
            const keys = await caches.keys();
            await Promise.all(keys.map(k => caches.delete(k).catch(() => {})));
          } catch (_) {}
        }
      } finally {
        try {
          url.searchParams.delete('nuke');
          url.searchParams.set('bust', 'mcm-' + Date.now());
          location.replace(url.pathname + (url.searchParams.toString() ? ('?' + url.searchParams.toString()) : ''));
        } catch (_) {
          location.reload();
        }
      }
    })();
  } catch (_) {}
})();
