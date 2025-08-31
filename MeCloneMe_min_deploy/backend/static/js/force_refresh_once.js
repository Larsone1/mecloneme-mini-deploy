<!doctype html>
<meta charset="utf-8">
<title>Force refresh once</title>
<pre>Clearing service workers & caches…</pre>
<script>
(async () => {
  try {
    if ('serviceWorker' in navigator) {
      const regs = await navigator.serviceWorker.getRegistrations();
      for (const r of regs) try { await r.unregister(); } catch(e){}
    }
    if ('caches' in window) {
      const keys = await caches.keys();
      await Promise.all(keys.map(k => caches.delete(k)));
    }
  } catch(e) {}
  const url = new URL(window.location.origin + '/onboarding');
  url.searchParams.set('bust', 'mcm-250831-oneshot');
  window.location.replace(url.toString());
})();
</script>
