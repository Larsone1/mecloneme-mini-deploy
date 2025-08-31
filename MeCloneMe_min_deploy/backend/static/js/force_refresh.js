// Unregister any old Service Workers once per version, then reload once.
(function(){
  const V = '25083104';
  try {
    if (localStorage.getItem('mcm_swfix') !== V) {
      if ('serviceWorker' in navigator) {
        navigator.serviceWorker.getRegistrations().then(rs => {
          return Promise.all(rs.map(r => r.unregister()));
        }).then(()=>{
          localStorage.setItem('mcm_swfix', V);
          // Force network reload
          location.replace(location.pathname + location.search + (location.search ? '&' : '?') + 'cb=' + V);
        });
      }
    }
  } catch(e) {
    console.warn('SW fix error', e);
  }
})();