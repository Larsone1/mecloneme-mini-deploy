import { useEffect, useRef, useState } from 'react'

const API = import.meta.env.VITE_API_URL || 'http://localhost:8000'
console.info('MCM API URL:', API)

const steps = [
  { id: 1, title: 'Zgody', desc: 'Nadaj uprawnienia: kamera + mikrofon (demo).' },
  { id: 2, title: 'Selfie', desc: 'Zrób selfie / wybierz plik; zapis w sesji.' },
  { id: 3, title: 'Głos',  desc: 'Chat + TTS/STT + nagranie i upload (sesja).' },
]

export default function App() {
  const [i, setI] = useState(0)
  const [sid, setSid] = useState(()=> localStorage.getItem('mcm_sid') || '')
  const [health, setHealth] = useState('⏳ sprawdzam…')
  const [speaking, setSpeaking] = useState(false)

  // TTS
  const [sayText, setSayText] = useState('Cześć! Jestem superklon MeCloneMe.')
  const [voices, setVoices] = useState([]); const [voiceName, setVoiceName] = useState('')
  const [rate, setRate] = useState(1.0); const [pitch = 1.0, setPitch] = useState(1.0)
  const [speakOnReply, setSpeakOnReply] = useState(true)

  // STT
  const [recogOn, setRecogOn] = useState(false); const [recogLang, setRecogLang] = useState('pl-PL')
  const [transcript, setTranscript] = useState(''); const recogRef = useRef(null)

  // Mic upload
  const [recState, setRecState] = useState('idle'); const [uploadMsg, setUploadMsg] = useState('')
  const mediaRef = useRef(null); const chunksRef = useRef([])

  // Chat + files
  const [input, setInput] = useState('')
  const [messages, setMessages] = useState([])
  const [files, setFiles] = useState({audio:[], image:[]})

  const s = steps[i]; const pct = ((i + 1) / steps.length) * 100

  useEffect(() => {
    fetch(`${API_BASE}/api/health`).then(r=>r.json()).then(
      () => setHealth('✅ backend OK'),
      () => setHealth('❌ backend OFF')
    )
    if (!sid) {
      fetch(`${API_BASE}/api`, {method:'POST'}).then(r=>r.json()).then(j=>{
        setSid(j.sid); localStorage.setItem('mcm_sid', j.sid)
      })
    }
  }, [])

  useEffect(() => {
    if (!sid) return
    fetch(`${API}/api/session/${sid}/history`).then(r=>r.json()).then(j=>setMessages(j.history||[])).catch(()=>{})
    fetch(`${API}/api/files?sid=${sid}`).then(r=>r.json()).then(setFiles).catch(()=>{})
  }, [sid])

  // TTS voices
  useEffect(() => {
    const load = () => {
      const v = window.speechSynthesis?.getVoices?.() || []
      setVoices(v)
      if (!voiceName) { const pref = v.find(x => /pl-|Pol/i.test(x.lang)) || v[0]; if (pref) setVoiceName(pref.name) }
    }
    load()
    window.speechSynthesis?.addEventListener?.('voiceschanged', load)
    return () => window.speechSynthesis?.removeEventListener?.('voiceschanged', load)
  }, [voiceName])

  const speak = (text) => {
    const u = new SpeechSynthesisUtterance(text ?? sayText)
    u.rate = rate; u.pitch = pitch
    u.onstart = ()=> setSpeaking(true)
    u.onend = ()=> setSpeaking(false)
    const v = voices.find(v => v.name === voiceName); if (v) u.voice = v
    window.speechSynthesis.cancel(); window.speechSynthesis.speak(u)
  }

  // STT
  const startSTT = () => {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition
    if (!SR) { setTranscript('❌ STT niedostępne'); return }
    const r = new SR(); r.lang = recogLang; r.interimResults = true; r.continuous = true
    r.onresult = (e) => { let t=''; for (let j=e.resultIndex;j<e.results.length;j++) t+=e.results[j][0].transcript; setTranscript(t.trim()) }
    r.onend = () => setRecogOn(false); recogRef.current = r; setRecogOn(true); r.start()
  }
  const stopSTT = () => recogRef.current?.stop?.()

  // Upload z SID
  const startRec = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mr = new MediaRecorder(stream); mediaRef.current = mr; chunksRef.current = []
      mr.ondataavailable = e => e.data && chunksRef.current.push(e.data)
      mr.onstop = async () => {
        try {
          const blob = new Blob(chunksRef.current, { type: mr.mimeType || 'audio/webm' })
          setUploadMsg('⏳ wysyłam…'); const fd = new FormData()
          fd.append('file', blob, `sample-${Date.now()}.webm`)
          if (sid) fd.append('sid', sid)
          const r = await fetch(`${API}/api/upload/audio`, { method: 'POST', body: fd })
          const j = await r.json(); setUploadMsg(`✅ ${Math.round(blob.size/1024)} KB → ${j.url}`); setRecState('sent')
          fetch(`${API}/api/files?sid=${sid}`).then(r=>r.json()).then(setFiles).catch(()=>{})
        } catch { setUploadMsg('❌ błąd uploadu'); setRecState('idle') }
        finally { stream.getTracks().forEach(t=>t.stop()) }
      }
      mr.start(); setRecState('rec')
    } catch { setUploadMsg('❌ brak dostępu do mikrofonu') }
  }
  const stopRec = () => { if (mediaRef.current && mediaRef.current.state==='recording') mediaRef.current.stop() }

  // Chat via API
  const send = async (text) => {
    const msg = (text ?? input ?? '').trim(); if (!msg || !sid) return
    setInput('')
    try {
      const r = await fetch(`${API}/api/chat/send`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({sid, text: msg}) })
      const j = await r.json()
      setMessages(j.history || [])
      if (speakOnReply) speak(j.reply || '')
    } catch {
      setMessages(m => [...m, {who:'user', text: msg}, {who:'bot', text:'❌ Błąd połączenia'}])
    }
  }

  async function softReset() {
    if (!sid) return;
    const fd = new FormData(); fd.append('sid', sid);
    await fetch(`${API}/api/session/soft-reset`, { method: 'POST', body: fd }).catch(()=>{});
    const j = await (await fetch(`${API}/api/session/${sid}/history`)).json().catch(()=>({history:[]}));
    setMessages(j.history || []);
  }

  async function downloadExport() {
    if (!sid) return;
    const r = await fetch(`${API}/api/session/export?sid=${sid}`);
    if (!r.ok) { alert('❌ Brak transkryptu'); return; }
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `${sid}-history.json`; a.click();
    URL.revokeObjectURL(url);
  }

  const handleImageUpload = async (e) => {
    const file = e.target.files?.[0];
    if (!file || !sid) return;
    const fd = new FormData();
    fd.append('file', file, file.name);
    fd.append('sid', sid);
    const r = await fetch(`${API}/api/upload/image`, {
      method: 'POST',
      body: fd,
    });
    const j = await r.json();
    alert(j.url ? `✅ Zapisano: ${j.url}` : 'OK');
    fetch(`${API}/api/files?sid=${sid}`).then(r => r.json()).then(setFiles).catch(() => {});
  };

  return (
    <div style={{minHeight:'100vh',display:'grid',placeItems:'center',background:'#0b0f17',color:'#fff',fontFamily:'Inter, system-ui, sans-serif'}}>
      <div style={{width:'min(960px,92vw)',background:'#111827',border:'1px solid #1f2937',borderRadius:16,boxShadow:'0 10px 40px rgba(0,0,0,.3)'}}>
        <div style={{padding:'12px 16px',borderBottom:'1px solid #1f2937',display:'flex',justifyContent:'space-between',alignItems:'center',gap:10}}>
          <div style={{display:'grid'}}>
            <strong>MeCloneMe — Onboarding</strong>
            <small style={{opacity:.75}}>SID: {sid || '⏳'}</small>
          </div>
          <div style={{display:'flex',gap:12,alignItems:'center'}}>
            <SpeakingDot on={speaking}/>
            <label style={{fontSize:12,opacity:.85,display:'flex',gap:6,alignItems:'center'}}>
              <input type="checkbox" checked={speakOnReply} onChange={e=>setSpeakOnReply(e.target.checked)} /> Autoodczyt
            </label>
            <div style={{opacity:.85,fontSize:12}}>{health}</div>
          </div>
        </div>

        <div style={{height:6,background:'#0f172a'}}><div style={{height:'100%',width:`${pct}%`,background:'#22d3ee',transition:'width .25s'}}/></div>

        <div style={{padding:24, display:'grid', gap:14}}>
          <h2 style={{margin:'4px 0 8px 0'}}>{s.title}</h2>
          <p style={{opacity:.85,margin:'0 0 6px 0'}}>{s.desc}</p>

          <> {/* Added Fragment */}
            {i===0 && (<div style={{display:'grid',gap:10}}><button style={btn}>Nadaj zgodę (demo)</button><small style={{opacity:.7}}>Makieta — nic nie zapisujemy.</small></div>)}

            {i===1 && (<div style={{display:'grid',gap:10}>
              <div style={drop}>
                <div>Upuść zdjęcie tutaj lub kliknij</div>
                <input type="file" accept="image/*" style={{display:'none'}} id="f" onChange={handleImageUpload}/>
              </div>
            </div>)}

            {i===2 && (
              <div style={{display:'grid',gap:14}}>
                <section style={card}>
                  <label style={label}>Chat — wyślij do klona</label>
                  <div style={{display:'flex',gap:10}}>
                    <input value={input} onChange={e=>setInput(e.target.value)} placeholder="Napisz wiadomość…" style={{...inputBox, flex:1}}/>
                    <button style={btn} onClick={()=>send()}>Wyślij</button>
                  </div>
                  <div style={{display:'grid',gap:8,marginTop:6}}>
                    {messages.map((m,idx)=>(<div key={idx} style={{opacity:.95}}><b>{m.who==='user'?'Ty':'Klon'}:</b> {m.text}</div>))}
                  </div>
                </section>

                <section style={card}>
                  <label style={label}>Pliki w sesji</label>
                  <div style={{display:'grid',gap:6}}>
                    <b>Audio ({files.audio?.length||0}):</b>
                    {files.audio.length? files.audio.map(f=><div key={f.url}><a href={f.url} target="_blank">{f.name}</a> — {Math.round(f.bytes/1024)} KB</div>): <span>—</span>}
                    <b style={{marginTop:8}}>Obrazy ({files.image?.length||0}):</b>
                    {files.image.length? files.image.map(f=><div key={f.url}><a href={f.url} target="_blank">{f.name}</a> — {Math.round(f.bytes/1024)} KB</div>): <span>—</span>}
                  </div>
                </section>

                <section style={card}>
                  <label style={label}>TTS / STT / Nagranie</label>
                  <div style={{display:'grid',gap:8,gridTemplateColumns:'1fr 1fr 1fr'}}>
                    <select value={voiceName} onChange={e=>setVoiceName(e.target.value)} style={input}>
                      {voices.map(v => <option key={v.name} value={v.name}>{v.name} ({v.lang})</option>)}
                    </select>
                    <label style={mini}>Rate {rate.toFixed(2)}<input type="range" min="0.75" max="1.25" step="0.01" value={rate} onChange={e=>setRate(+e.target.value)} /></label>
                    <label style={mini}>Pitch {pitch.toFixed(2)}<input type="range" min="0.8" max="1.2" step="0.01" value={pitch} onChange={e=>setPitch(+e.target.value)} /></label>
                  </div>
                  <div style={{display:'flex',gap:10}}>
                    <button style={btn} onClick={()=>speak()}>▶️ Powiedz</button>
                    {!recogOn ? <button style={btn} onClick={startSTT}>🎤 Start STT</button> : <button style={{...btn,background:'#b91c1c'}} onClick={stopSTT}>■ Stop STT</button>}
                    <button style={btn} onClick={()=>setInput(transcript)}>↗️ Użyj transkrypcji</button>
                    <button style={{...btn,background: recState==='rec' ? '#b91c1c' : '#1f2937'}} onClick={recState==='rec' ? stopRec : startRec}>{recState==='rec' ? '■ Stop' : '🎙️ Start'}</button>
                  </div>
                  <small style={{opacity:.8}}>{uploadMsg}</small>
                </section>

                <section style={card}>
                  <label style={label}>Debug</label>
                  <div style={{display:'grid',gap:6}}>
                    <div>API: <code>{API}</code></div>
                    <div>SID: <code>{sid||'...'}</code></div>
                    <button style={btn} onClick={()=>navigator.clipboard?.writeText(API)}>Kopiuj API URL</button>
                    <button style={btn} onClick={downloadExport}>Pobierz transkrypt (.json)</button>
                  </div>
                </section>
              </div>
            )}
          </> {/* Added Fragment */}
        </div>

        <div style={{display:'flex',gap:10,justifyContent:'space-between',padding:20,borderTop:'1px solid #1f2937'}}>
          <div style={{display:'flex',gap:10}}>
            <button style={{...btn,opacity:i===0?.5:1}} disabled={i===0} onClick={()=>setI(x=>Math.max(0,x-1))}>← Wstecz</button>
            {i<steps.length-1
              ? <button style={btn} onClick={()=>setI(x=>Math.min(steps.length-1,x+1))}>Dalej →</button>
              : <button style={{...btn,background:'#22c55e'}} onClick={()=>alert('Superklon: DEMO zakończone ✅')}>Zakończ</button>}
          </div>
          <div style={{display:'flex',gap:10}}>
            <button style={{...btn,background:'#b45309'}} onClick={softReset}>Reset sesji (zostaw pliki)</button>
            <button style={{...btn,background:'#0e7490'}} onClick={()=>localStorage.removeItem('mcm_sid') || location.reload()}>Nowa sesja (nowy SID)</button>
          </div>
        </div>
      </div>
    </div>
  )
}

function SpeakingDot({on}){return <span style={{width:10,height:10,borderRadius:'50%',background:on?'#22d3ee':'#334155',boxShadow:on?'0 0 12px #22d3ee':'none',display:'inline-block'}}/>}
const baseBox = { border:'1px solid #23304a', borderRadius:12, background:'#0b1220' }
const btn = { background:'#1f2937', color:'#fff', border:'1px solid #2b364a', padding:'10px 14px', borderRadius:12, cursor:'pointer' }
const drop = { height:120, ...baseBox, display:'grid', placeItems:'center', cursor:'pointer' }
const card = { display:'grid', gap:10, padding:12, ...baseBox }
const label = { opacity:.85, fontSize:13 }
const input = { background:'#0b1220', border:'1px solid #23304a', color:'#fff', padding:'10px 12px', borderRadius:12 }
const mini = { ...input, padding:'6px 8px' }
const inputBox = { background:'#0b1220', border:'1px solid #23304a', color:'#fff', padding:'10px 12px', borderRadius:12 }
