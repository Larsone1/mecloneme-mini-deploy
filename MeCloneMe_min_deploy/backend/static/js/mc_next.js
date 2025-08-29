// Turn on the gradient on these routes (edit this list if needed)
const MCM_GRAD_ROUTES = new Set(['/mobile', '/start', '/comm/mobile']);
document.addEventListener('DOMContentLoaded', () => {
  const path = window.location.pathname.replace(/\/$/, '');
  if (MCM_GRAD_ROUTES.has(path) || (path==='' && MCM_GRAD_ROUTES.has('/'))) {
    document.body.classList.add('mcm-gradient-mobile');
  }
});
