// Bootstrap PWA (manifest+SW) and hide site header in standalone
(function(){
  try{
    const head = document.head;
    const linkM = document.createElement('link'); linkM.rel='manifest'; linkM.href='/static/manifest.webmanifest'; head.appendChild(linkM);
    const theme = document.createElement('meta'); theme.name='theme-color'; theme.content='#000000'; head.appendChild(theme);
    const mm1 = document.createElement('meta'); mm1.name='apple-mobile-web-app-capable'; mm1.content='yes'; head.appendChild(mm1);
    const mm2 = document.createElement('meta'); mm2.name='apple-mobile-web-app-status-bar-style'; mm2.content='black-translucent'; head.appendChild(mm2);
    const mm3 = document.createElement('meta'); mm3.name='apple-mobile-web-app-title'; mm3.content='MeCloneMe'; head.appendChild(mm3);
    const icon = document.createElement('link'); icon.rel='apple-touch-icon'; icon.href='/static/img/logo_mecloneme.png'; head.appendChild(icon);
    const linkCss = document.createElement('link'); linkCss.rel='stylesheet'; linkCss.href='/static/css/mc_pwa.css'; head.appendChild(linkCss);
    if ('serviceWorker' in navigator) { window.addEventListener('load', ()=> navigator.serviceWorker.register('/static/js/sw.js').catch(()=>{})); }
    function update(){ const s = window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone===true; document.body.classList.toggle('pwa-standalone', s); }
    var mq = window.matchMedia('(display-mode: standalone)'); if (mq && mq.addEventListener) mq.addEventListener('change', update); document.addEventListener('DOMContentLoaded', update);
  }catch(e){}
})();
