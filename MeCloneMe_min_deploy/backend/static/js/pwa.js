(function () {
  try {
    var isStandalone = window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone;
    var ENABLE_SW = !/disableSW=1/.test(location.search);
    if (ENABLE_SW && 'serviceWorker' in navigator) {
      navigator.serviceWorker.getRegistration().then(function (reg) {
        if (!reg) {
          navigator.serviceWorker.register('/static/sw.js').catch(function(){});
        }
      });
    }
  } catch (e) { console.warn('pwa bootstrap skipped', e); }
})();