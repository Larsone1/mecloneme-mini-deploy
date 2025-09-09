import { useEffect, useState } from 'react'
const API = import.meta.env.VITE_API_URL || 'http://localhost:8000'
const steps = [
  { id: 1, title: 'Zgody', desc: 'Nadaj uprawnienia: kamera + mikrofon (demo).' },
  { id: 2, title: 'Selfie', desc: 'Zrób selfie / wybierz plik (UI placeholder).' },
  { id: 3, title: 'Głos',  desc: 'Nagraj 5–10 s próbkę (UI placeholder).' },
]
export default function App() {
  const [i, setI] = useState(0)
  const [health, setHealth] = useState('⏳ sprawdzam…')
  const s = steps[i]; const pct = ((i + 1) / steps.length) * 100
  useEffect(() => {
    fetch(`${API}/api/health`).then(r=>r.json()).then(
      () => setHealth('✅ backend OK'),
      () => setHealth('❌ backend OFF')
    )
  }, [])
  return (
    <div style={{minHeight:'100vh',display:'grid',placeItems:'center',background:'#0b0f17',color:'#fff',fontFamily:'Inter, system-ui, sans-serif'}}>
      <div style={{width:'min(720px,90vw)',background:'#111827',border:'1px solid #1f2937',borderRadius:16,boxShadow:'0 10px 40px rgba(0,0,0,.3)'}}>
        <div style={{padding:'12px 16px',borderBottom:'1px solid #1f2937',display:'flex',justifyContent:'space-between',alignItems:'center'}}>
          <div style={{fontWeight:700,letterSpacing:.3}}>MeCloneMe — Onboarding</div>
          <div style={{opacity:.85,fontSize:12}}>{health}</div>
        </div>
        <div style={{height:6,background:'#0f172a'}}><div style={{height:'100%',width:`${pct}%`,background:'#22d3ee',transition:'width .25s'}}/></div>
        <div style={{padding:24}}>
          <h2 style={{margin:'4px 0 8px 0'}}>{s.title}</h2>
          <p style={{opacity:.85,margin:'0 0 18px 0'}}>{s.desc}</p>
          {i===0 && (<div style={{display:'grid',gap:10}}><button style={btn}>Nadaj zgodę (demo)</button><small style={{opacity:.7}}>Makieta — nic nie zapisujemy.</small></div>)}
          {i===1 && (<div style={{display:'grid',gap:10}}><div style={drop}><div>Upuść zdjęcie tutaj lub kliknij</div><input type="file" accept="image/*" style={{display:'none'}} id="f" onChange={()=>{}}/></div></div>)}
          {i===2 && (<div style={{display:'grid',gap:10}}><button style={btn}>● Nagraj próbkę (demo)</button><div style={{height:80,background:'#0b1220',border:'1px dashed #23304a',borderRadius:12,display:'grid',placeItems:'center',opacity:.8}}>Wizualizacja fali (placeholder)</div></div>)}        </div>
        <div style={{display:'flex',gap:10,justifyContent:'space-between',padding:20,borderTop:'1px solid #1f2937'}}>
          <button style={{...btn,opacity:i===0?.5:1}} disabled={i===0} onClick={()=>setI(x=>Math.max(0,x-1))}>← Wstecz</button>
          {i<steps.length-1 ? (<button style={btn} onClick={()=>setI(x=>Math.min(steps.length-1,x+1))}>Dalej →</button>) :
            (<button style={{...btn,background:'#22c55e'}} onClick={()=>alert('Superklon: DEMO zakończone ✅')}>Zakończ</button>)}
        </div>
      </div>
    </div>
  )
}
const btn = { background:'#1f2937', color:'#fff', border:'1px solid #2b364a', padding:'10px 14px', borderRadius:12, cursor:'pointer' }
const drop = { height:120, background:'#0b1220', border:'1px dashed #23304a', borderRadius:12, display:'grid', placeItems:'center', cursor:'pointer' }
