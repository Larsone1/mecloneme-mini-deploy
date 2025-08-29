// MCM NEXT — enable mobile welcome gradient without touching templates
// Routes where gradient is active (edit if needed):
const MCM_GRAD_ROUTES = new Set(['/mobile', '/start']);

document.addEventListener('DOMContentLoaded', () => {
  try{
    const path = window.location.pathname.replace(/\/$/, '');
    if (MCM_GRAD_ROUTES.has(path) || (path==='' && MCM_GRAD_ROUTES.has('/'))){
      document.body.classList.add('mcm-gradient-mobile');
    }
  }catch(e){ /* no-op */ }
});
