(function(){
  if (!('serviceWorker' in navigator)) return;
  window.addEventListener('load', function(){
    // rejestracja istniejącego SW
    navigator.serviceWorker.register('/static/sw.js').catch(function(e){console.log('SW reg error', e)});
  });
})();