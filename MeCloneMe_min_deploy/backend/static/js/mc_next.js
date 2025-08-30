// Force 3-point gradient via JS (no external CSS).
// Angle: 346.7deg (13.3° left from vertical). Stops: 0% #000000, 51% #00296B, 100% #000000.
(function(){
  function applyGrad(){
    try {
      var p = (location.pathname || '/').replace(/\/$/, '');
      var ok = p==='' || p==='/' || p==='/start' || p==='/comm/mobile' || p==='/mobile';
      if (!ok) return;
      var s = document.body && document.body.style; if (!s) return;
      s.minHeight = '100svh';
      s.background = 'linear-gradient(346.7deg, #000000 0%, #00296B 51%, #000000 100%)';
      s.backgroundAttachment = 'fixed';
    } catch(e){}
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', applyGrad);
  } else {
    applyGrad();
  }
})();
