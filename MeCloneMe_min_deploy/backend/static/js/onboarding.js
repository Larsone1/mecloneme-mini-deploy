// Minimal client for onboarding MVP
const api = (p,opts={})=>fetch(p,opts).then(r=>r.json());
function sid(){let s=localStorage.getItem('mcm_sid'); if(!s){s=Math.random().toString(36).slice(2); localStorage.setItem('mcm_sid',s);} return s;}
function show(id){document.querySelectorAll('main > section').forEach(x=>x.classList.add('hidden')); document.querySelector(id).classList.remove('hidden'); window.scrollTo({top:0,behavior:'smooth'});}

document.addEventListener('click',(e)=>{const n=e.target.dataset.next;if(n){show(n);} const b=e.target.dataset.back;if(b){show(b);}});

document.addEventListener('DOMContentLoaded',()=>{
  // start
  document.getElementById('startBtn').onclick = async ()=>{
    const payload={
      email: email.value||null, dob: dob.value||null, ref: ref.value||null,
      consents: {tou:c_tou.checked, listen:c_listen.checked, posthumous:c_posthumous.checked},
      sid: sid()
    };
    if(!payload.consents.tou){ alert('Wymagana zgoda na Regulamin/RODO'); return; }
    await api('/api/clone/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    show('#step-voice');
  };

  // voice record
  const recBtns=document.querySelectorAll('.mic[data-idx]'); let mediaRecorder=null,chunks=[],currentIdx=null;
  async function startRec(){const stream=await navigator.mediaDevices.getUserMedia({audio:true});
    mediaRecorder=new MediaRecorder(stream); chunks=[];
    mediaRecorder.ondataavailable=e=>chunks.push(e.data);
    mediaRecorder.onstop=async ()=>{const blob=new Blob(chunks,{type:'audio/webm'});
      const fd=new FormData(); fd.append('sid',sid()); fd.append('idx',currentIdx); fd.append('file',blob,`rec_${currentIdx}.webm`);
      const r=await fetch('/api/clone/voice',{method:'POST',body:fd}); const j=await r.json(); pVoice.style.width=j.progress+'%';
    }; mediaRecorder.start();}
  recBtns.forEach(btn=>btn.addEventListener('click',async()=>{
    if(!mediaRecorder || mediaRecorder.state==='inactive'){btn.classList.add('active'); currentIdx=btn.dataset.idx; await startRec();}
    else{mediaRecorder.stop(); btn.classList.remove('active');}
  }));
  voiceFiles.addEventListener('change',async(e)=>{
    const files=[...e.target.files||[]]; for(const f of files){const fd=new FormData(); fd.append('sid',sid()); fd.append('file',f);
      const r=await fetch('/api/clone/voice',{method:'POST',body:fd}); const j=await r.json(); pVoice.style.width=j.progress+'%';}
  });

  // face uploads
  photos.addEventListener('change',async(e)=>{let prog=0; for(const f of [...e.target.files||[]]){
    const fd=new FormData(); fd.append('sid',sid()); fd.append('type','photo'); fd.append('file',f);
    const r=await fetch('/api/clone/photo',{method:'POST',body:fd}); const j=await r.json(); prog=j.progress||prog; pFace.style.width=prog+'%';}});
  video.addEventListener('change',async(e)=>{const f=e.target.files[0]; if(!f) return;
    const fd=new FormData(); fd.append('sid',sid()); fd.append('type','video'); fd.append('file',f);
    const r=await fetch('/api/clone/video',{method:'POST',body:fd}); const j=await r.json(); pFace.style.width=(j.progress||0)+'%';});

  // text samples
  document.querySelector('#step-text [data-next]').addEventListener('click',()=>{
    const s={sid:sid(),samples:[t1.value,t2.value,t3.value].filter(Boolean)};
    fetch('/api/clone/text',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(s)});
    summaryBox.innerHTML=`<b>Podsumowanie</b><br>Email: ${email.value||'-'}<br>DOB: ${dob.value||'-'}<br>Ref: ${ref.value||'-'}`;
  });

  // train
  trainBtn.onclick=async()=>{
    await api('/api/clone/train',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sid:sid()})});
    show('#step-status'); const t=setInterval(async()=>{const j=await api('/api/clone/status?sid='+sid());
      pTrain.style.width=(j.progress||0)+'%'; statusText.textContent=j.message||''; if(j.progress>=100) clearInterval(t);},1200);
  };

  toTest.onclick=()=>show('#step-test');
  askBtn.onclick=()=>{const q=testInput.value.trim(); if(!q) return; chatBox.innerHTML+=`<div><b>Ty:</b> ${q}</div><div><b>Klon:</b> ${q} — (demo)</div>`; testInput.value='';
    const u=new SpeechSynthesisUtterance('To jest demo odpowiedź klona.'); speechSynthesis.speak(u); };
});
