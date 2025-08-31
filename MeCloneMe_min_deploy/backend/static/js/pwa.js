(()=>{
  const V='mcm-20250831-1320';
  const register = async () => {
    if(!('serviceWorker' in navigator)) return;
    try{
      const reg = await navigator.serviceWorker.register('/static/sw.js?v='+V,{scope:'/'});
      console.log('[MCM:PWA] SW registered', reg.scope);
    }catch(e){
      console.error('[MCM:PWA] SW registration failed', e);
    }
  };
  window.addEventListener('load', register);
})();