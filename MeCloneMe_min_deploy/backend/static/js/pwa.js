// mcm hotfix mcm-20250831-110605-utc
// Robust SW registration that *always* checks for updates.
(function() {
  if (!('serviceWorker' in navigator)) return;

  const swUrl = '/static/sw.js?v=mcm-20250831-110605-utc'; // cache-busting

  window.addEventListener('load', () => {
    navigator.serviceWorker.register(swUrl).then(reg => {
      // Ask a waiting SW to take control immediately
      if (reg.waiting) reg.waiting.postMessage({ type: 'SKIP_WAITING' });

      // Periodically ask for updates while the app is open
      try {
        setInterval(() => reg.update(), 10 * 1000);
      } catch (e) { /* ignore */ }

      // When a new SW takes control, reload to get fresh HTML/CSS/JS
      navigator.serviceWorker.addEventListener('controllerchange', () => window.location.reload());
    }).catch(console.warn);
  });
})();
