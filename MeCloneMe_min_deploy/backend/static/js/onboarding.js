(function(){
  const steps = Array.from(document.querySelectorAll('.mcm-step'));
  const meter = document.getElementById('mcmMeter');
  const state = JSON.parse(localStorage.getItem('mcmOnb')||'{}');
  const setStep = (n)=>{
    steps.forEach(s => s.classList.toggle('active', +s.dataset.step===n));
    meter.style.width = ((n-1)/(steps.length-1))*100 + '%';
  };
  setStep(1);

  // step1
  document.getElementById('step1next').addEventListener('click',()=>{
    const name = document.getElementById('inpName').value.trim();
    if(!name){ alert('Podaj imię'); return; }
    state.name = name; localStorage.setItem('mcmOnb', JSON.stringify(state));
    setStep(2);
  });

  // chips
  document.querySelectorAll('.mcm-chip').forEach(ch=>{
    ch.addEventListener('click',()=> ch.classList.toggle('active'));
  });

  // nav buttons
  document.querySelectorAll('[data-next]').forEach(b=> b.addEventListener('click',()=>{
    const cur = +b.closest('.mcm-step').dataset.step;
    if(cur===2){ state.goals = Array.from(document.querySelectorAll('[data-field="goals"] .mcm-chip.active')).map(x=>x.textContent); }
    if(cur===3){ state.modes = Array.from(document.querySelectorAll('[data-field="modes"] .mcm-chip.active')).map(x=>x.textContent); }
    if(cur===4){ state.lang = document.getElementById('lang').value; state.tone=document.getElementById('tone').value; }
    if(cur===5){
      state.g1=document.getElementById('g1').checked;
      state.g2=document.getElementById('g2').checked;
      state.g3=document.getElementById('g3').checked;
      if(!state.g1){ alert('Musisz zaakceptować Regulamin/RODO'); return; }
      // Prosta logika tieru
      const goals = (state.goals||[]).length;
      const modes = (state.modes||[]).length;
      state.tier = (goals>=2 && modes>=2) ? 'Creator' : 'Basic';
      localStorage.setItem('mcmOnb', JSON.stringify(state));
    }
    const next = Math.min(cur+1, steps.length);
    if(next===6){
      document.getElementById('summaryName').textContent = state.name ? (state.name + '!') : '';
      document.getElementById('accountTier').textContent = state.tier || 'Basic';
    }
    setStep(next);
  }));
  document.querySelectorAll('[data-prev]').forEach(b=> b.addEventListener('click',()=>{
    const cur = +b.closest('.mcm-step').dataset.step;
    setStep(Math.max(1, cur-1));
  }));

  // tiny helper for returning users
  if(state.name){ document.getElementById('inpName').value = state.name; }
})();
