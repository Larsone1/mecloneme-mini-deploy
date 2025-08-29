// PWA bootstrap — inject manifest & meta, register SW, and hide site header in standalone.
(function(){
  try {
    // 1) Inject manifest + meta
    const head = document.head || document.getElementsByTagName('head')[0];
    const linkM = document.createElement('link');
    linkM.rel = 'manifest';
    linkM.href = '/static/manifest.webmanifest';
    head.appendChild(linkM);

    const theme = document.createElement('meta');
    theme.name = 'theme-color';
    theme.content = '#000000';
    head.appendChild(theme);

    // iOS PWA hints
    const mm1 = document.createElement('meta');
    mm1.name = 'apple-mobile-web-app-capable';
    mm1.content = 'yes';
    head.appendChild(mm1);

    const mm2 = document.createElement('meta');
    mm2.name = 'apple-mobile-web-app-status-bar-style';
    mm2.content = 'black-translucent';
    head.appendChild(mm2);

    const mm3 = document.createElement('meta');
    mm3.name = 'apple-mobile-web-app-title';
    mm3.content = 'MeCloneMe';
    head.appendChild(mm3);

    const linkIcon = document.createElement('link');
    linkIcon.rel = 'apple-touch-icon';
    linkIcon.href = '/static/img/logo_mecloneme.png';
    head.appendChild(linkIcon);

    // 2) Load PWA-specific CSS (header hiding in standalone)
    const linkCss = document.createElement('link');
    linkCss.rel = 'stylesheet';
    linkCss.href = '/static/css/mc_pwa.css';
    head.appendChild(linkCss);

    // 3) Register the Service Worker (scope limited to /static/js but good enough for installability)
    if ('serviceWorker' in navigator) {
      window.addEventListener('load', function() {
        navigator.serviceWorker.register('/static/js/sw.js').catch(()=>{});
      });
    }

    // 4) Toggle body class when running as standalone (Android/iOS)
    function updateStandaloneClass(){
      const isStandalone = window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
      document.body.classList.toggle('pwa-standalone', isStandalone);
    }
    var mq = window.matchMedia('(display-mode: standalone)');
    if (mq && mq.addEventListener) mq.addEventListener('change', updateStandaloneClass);
    document.addEventListener('DOMContentLoaded', updateStandaloneClass);
  } catch(e) { /* no-op */ }
})();