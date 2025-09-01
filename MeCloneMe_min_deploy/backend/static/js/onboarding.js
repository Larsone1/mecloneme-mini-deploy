// MCM Hotfix 2025-09-01: minimal onboarding controller
(function(){
  try {
    document.addEventListener('DOMContentLoaded', function(){
      var steps = Array.prototype.slice.call(document.querySelectorAll('.mcm-step'));
      if (!steps.length) return;
      var current = steps.findIndex(function(s){ return s.classList.contains('active'); });
      if (current < 0) { current = 0; steps[0].classList.add('active'); }

      function show(i){
        if (i < 0) i = 0;
        if (i >= steps.length) i = steps.length - 1;
        steps.forEach(function(s, idx){ s.classList.toggle('active', idx === i); });
        current = i;
        // update progress bar if present
        var progress = document.querySelector('[data-progress]');
        if (progress) {
          var pct = Math.round(((current+1) / steps.length) * 100);
          progress.style.width = pct + '%';
          progress.setAttribute('aria-valuenow', pct);
        }
      }

      document.querySelectorAll('[data-next]').forEach(function(btn){
        btn.addEventListener('click', function(ev){
          ev.preventDefault();
          show(current + 1);
        });
      });
      document.querySelectorAll('[data-prev]').forEach(function(btn){
        btn.addEventListener('click', function(ev){
          ev.preventDefault();
          show(current - 1);
        });
      });

      // Fallback: ensure first step visible after 50ms in case CSS order conflicts
      setTimeout(function(){ show(current); }, 50);
    });
  } catch(e) {
    console && console.error && console.error('[MCM] onboarding hotfix error', e);
  }
})();
