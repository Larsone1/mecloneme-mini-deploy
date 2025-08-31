(function(){
  const logo = document.getElementById('mcmLogo');
  const powered = document.getElementById('mcmPowered');
  const delay = (ms)=>new Promise(r=>setTimeout(r,ms));

  async function run(){
    await delay(1500);
    logo.classList.add('show');          // fade-in (4s CSS)
    await delay(4000 + 3000);            // hold 3s after fade
    powered.classList.add('show');       // slide up
    await delay(1200);
    window.location.replace('/onboarding?v=' + Date.now());
  }
  run();
})();
