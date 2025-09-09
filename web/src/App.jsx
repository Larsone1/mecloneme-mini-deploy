import { useEffect, useRef, useState } from 'react'

const API = import.meta.env.VITE_API_URL || 'http://localhost:8000'
const steps = [
  { id: 1, title: 'Zgody', desc: 'Nadaj uprawnienia: kamera + mikrofon (demo).' },
  { id: 2, title: 'Selfie', desc: 'Zrób selfie / wybierz plik (UI placeholder).' },
  { id: 3, title: 'Głos',  desc: 'TTS + STT + nagranie i upload + chat (demo).' },
]

export default function App() {
  const [i, setI] = useState(0)
  const [health, setHealth] = useState('⏳ sprawdzam…')

  // TTS
  const [sayText, setSayText] = useState('Cześć! Jestem superklon MeCloneMe.')
  const [voices, setVoices] = useState([])
  const [voiceName, setVoiceName] = useState('')
  const [rate, setRate] = useState(1.0)
  const [pitch, setPitch] = useState(1.0)

  // STT
  const [recogOn, setRecogOn] = useState(false)
  const [recogLang, setRecogLang] = useState('pl-PL')
  const [transcript, setTranscript] = useState('')
  const recogRef = useRef(null)

  // Mic upload
  const [recState, setRecState] = useState('idle')
  const [uploadMsg, setUploadMsg] = useState('')
  const mediaRef = useRef(null)
  const chunksRef = useRef([])

  // Chat
  const [input, setInput] = useState('')
  const [messages, setMessages] = useState([]) // {who:'user'|'bot', text:string}

  const s = steps[i]
  const pct = ((i + 1) / steps.length) * 100

  useEffect(() => {
    fetch(`${API}/api/health`).then(r=>r.json()).then(
      () => setHealth('✅ backend OK'),
      () => setHealth('❌ backend OFF')
    )
  }, [])

  // Load TTS voices
  useEffect(() => {
    const load = () => {
      const v = window.speechSynthesis?.getVoices?.() || []
      setVoices(v)
      if (!voiceName) {
        const pref = v.find(x => /pl-|Pol/i.test(x.lang)) || v[0]
        if (pref) setVoiceName(pref.name)
      }
    }
    load()
    window.speechSynthesis?.addEventListener?.('voiceschanged', load)
    return () => window.speechSynthesis?.removeEventListener?.('voiceschanged', load)
  }, [voiceName])

  const speak = (text) => {
    const u = new SpeechSynthesisUtterance(text ?? sayText)
    u.rate = rate; u.pitch = pitch
    const v = voices.find(v => v.name === voiceName); if (v) u.voice = v
    window.speechSynthesis.cancel(); window.speechSynthesis.speak(u)
  }

  // STT
  const startSTT = () => {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition
    if (!SR) { setTranscript('❌ STT niedostępne'); return }
    const r = new SR()
    r.lang = recogLang; r.interimResults = true; r.continuous = true
    r.onresult = (e) => {
      let text = ''
      for (let j = e.resultIndex; j < e.results.length; j++) text += e.results[j][0].transcript
      setTranscript(text.trim())
    }
    r.onend = () => setRecogOn(false)
    recogRef.current = r; setRecogOn(true); r.start()
  }
  const stopSTT = () => recogRef.current?.stop?.()
  useEffect(() => { if (!recogOn) recogRef.current?.stop?.() }, [recogOn])

  // Mic record + upload
  const startRec = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mr = new MediaRecorder(stream)
      mediaRef.current = mr; chunksRef.current = []
      mr.ondataavailable = e => e.data && chunksRef.current.push(e.data)
      mr.onstop = async () => {
        try {
          const blob = new Blob(chunksRef.current, { type: mr.mimeType || 'audio/webm' })
          setUploadMsg('⏳ wysyłam…')
          const fd = new FormData(); fd.append('file', blob, `sample-${Date.now()}.webm`)
          const r = await fetch(`${API}/api/upload/audio`, { method: 'POST', body: fd })
          const j = await r.json()
          setUploadMsg(`✅ ${Math.round(blob.size/1024)} KB → serwer: ${j.received_bytes} B`)
          setRecState('sent')
        } catch { setUploadMsg('❌ błąd uploadu'); setRecState('idle') }
        finally { stream.getTracks().forEach(t=>t.stop()) }
      }
      mr.start(); setRecState('rec')
    } catch { setUploadMsg('❌ brak dostępu do mikrofonu') }
  }
  const stopRec = () => { if (mediaRef.current && mediaRef.current.state==='recording') mediaRef.current.stop() }

  // Chat send
  const send = async (text) => {
    const msg = (text ?? input ?? '').trim(); if (!msg) return
    setMessages(m => [...m, {who:'user', text: msg}]); setInput('')
    try {
      const r = await fetch(`${API}/api/reply`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({text: msg}) })
      const j = await r.json()
      const bot = j.reply || 'OK — zapisane.'
      setMessages(m => [...m, {who:'bot', text: bot}])
      speak(bot)
    } catch {
      const bot = '❌ Błąd połączenia – spróbuj ponownie.'
      setMessages(m => [...m, {who:'bot', text: bot}])
    }
  }

  return (
    <div style={{minHeight:'100vh',display:'grid',placeItems:'center',background:'#0b0f17',color:'#fff',fontFamily:'Inter, system-ui, sans-serif'}}>
      <div style={{width:'min(860px,92vw)',background:'#111827',border:'1px solid #1f2937',borderRadius:16,boxShadow:'0 10px 40px rgba(0,0,0,.3)'}}>
        <div style={{padding:'12px 16px',borderBottom:'1px solid #1f2937',display:'flex',justifyContent:'space-between',alignItems:'center'}}>
          <div style={{fontWeight:700,letterSpacing:.3}}>MeCloneMe — Onboarding</div>
          <div style={{opacity:.85,fontSize:12}}>{health}</div>
        </div>

        <div style={{height:6,background:'#0f172a'}}><div style={{height:'100%',width:`${pct}%`,background:'#22d3ee',transition:'width .25s'}}/></div>

        <div style={{padding:24, display:'grid', gap:16}}>
          <h2 style={{margin:'4px 0 8px 0'}}>{s.title}</h2>
          <p style={{opacity:.85,margin:'0 0 6px 0'}}>{s.desc}</p>

          {i===0 && (<div style={{display:'grid',gap:10}}><button style={btn}>Nadaj zgodę (demo)</button><small style={{opacity:.7}}>Makieta — nic nie zapisujemy.</small></div>)}

          {i===1 && (<div style={{display:'grid',gap:10}}><div style={drop}><div>Upuść zdjęcie tutaj lub kliknij</div><input type="file" accept="image/*" style={{display:'none'}} id="f" onChange={()=>{}}/></div></div>)}

          {i===2 && (
            <div style={{display:'grid',gap:14}}>
              <section style={card}>
                <label style={label}>TTS — mowa klona</label>
                <input value={sayText} onChange={e=>setSayText(e.target.value)} style={input}/>
                <div style={{display:'grid',gap:8,gridTemplateColumns:'1fr 1fr 1fr'}}>
                  <select value={voiceName} onChange={e=>setVoiceName(e.target.value)} style={input}>
                    {voices.map(v => <option key={v.name} value={v.name}>{v.name} ({v.lang})</option>)}
                  </select>
                  <label style={mini}>Rate {rate.toFixed(2)}<input type="range" min="0.75" max="1.25" step="0.01" value={rate} onChange={e=>setRate(+e.target.value)} /></label>
                  <label style={mini}>Pitch {pitch.toFixed(2)}<input type="range" min="0.8" max="1.2" step="0.01" value={pitch} onChange={e=>setPitch(+e.target.value)} /></label>
                </div>
                <button style={btn} onClick={()=>speak()}>▶️ Powiedz</button>
              </section>

              <section style={card}>
                <label style={label}>STT — rozpoznawanie mowy</label>
                <div style={{display:'flex',gap:10,alignItems:'center'}}>
                  <select value={recogLang} onChange={e=>setRecogLang(e.target.value)} style={inputSm}>
                    <option value="pl-PL">pl-PL</option>
                    <option value="en-US">en-US</option>
                  </select>
                  {!recogOn
                    ? <button style={btn} onClick={startSTT}>🎤 Start STT</button>
                    : <button style={{...btn,background:'#b91c1c'}} onClick={stopSTT}>■ Stop STT</button>}
                  <button style={btn} onClick={()=>setInput(transcript)}>↗️ Użyj transkrypcji</button>
                </div>
                <div style={{minHeight:60,background:'#0b1220',border:'1px dashed #23304a',borderRadius:12,padding:'10px 12px',opacity:.9}}>
                  {transcript || '—'}
                </div>
              </section>

              <section style={card}>
                <label style={label}>Chat — wyślij do klona</label>
                <div style={{display:'flex',gap:10}}>
                  <input value={input} onChange={e=>setInput(e.target.value)} placeholder="Napisz wiadomość…" style={{...inputBox, flex:1}}/>
                  <button style={btn} onClick={()=>send()}>Wyślij</button>
                </div>
                <div style={{display:'grid',gap:8}}>
                  {messages.map((m,idx)=>(<div key={idx} style={{opacity:.95}}><b>{m.who==='user'?'Ty':'Klon'}:</b> {m.text}</div>))}
                </div>
              </section>

              <section style={card}>
                <label style={label}>Nagranie + upload</label>
                <div style={{display:'flex',gap:10}}>
                  <button style={{...btn,background: recState==='rec' ? '#b91c1c' : '#1f2937'}}
                          onClick={recState==='rec' ? stopRec : startRec}>
                    {recState==='rec' ? '■ Stop' : '🎙️ Start'}
                  </button>
                </div>
                <small style={{opacity:.8}}>{uploadMsg}</small>
              </section>
            </div>
          )}
        </div>

        <div style={{display:'flex',gap:10,justifyContent:'space-between',padding:20,borderTop:'1px solid #1f2937'}}>
          <button style={{...btn,opacity:i===0?.5:1}} disabled={i===0} onClick={()=>setI(x=>Math.max(0,x-1))}>← Wstecz</button>
          {i<steps.length-1 ? (
            <button style={btn} onClick={()=>setI(x=>Math.min(steps.length-1,x+1))}>Dalej →</button>
          ) : (
            <button style={{...btn,background:'#22c55e'}} onClick={()=>alert('Superklon: DEMO zakończone ✅')}>Zakończ</button>
          )}
        </div>
      </div>
    </div>
  )
}

const baseBox = { border:'1px solid #23304a', borderRadius:12, background:'#0b1220' }
const btn = { background:'#1f2937', color:'#fff', border:'1px solid #2b364a', padding:'10px 14px', borderRadius:12, cursor:'pointer' }
const drop = { height:120, ...baseBox, display:'grid', placeItems:'center', cursor:'pointer' }
const card = { display:'grid', gap:10, padding:12, ...baseBox }
const label = { opacity:.85, fontSize:13 }
const input = { background:'#0b1220', border:'1px solid #23304a', color:'#fff', padding:'10px 12px', borderRadius:12 }
const inputSm = { ...input, padding:'8px 10px' }
const inputBox = { background:'#0b1220', border:'1px solid #23304a', color:'#fff', padding:'10px 12px', borderRadius:12 }
const mini = { opacity:.85, fontSize:11, display:'grid', gap:4 }