import { useEffect, useState } from 'react'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

export default function App() {
  const [ping, setPing] = useState('…')
  const [error, setError] = useState(null)

  useEffect(() => {
    const url = `${API_BASE}/alerts/health`
    fetch(url)
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        const txt = await r.text()
        setPing(txt || 'OK')
      })
      .catch((e) => setError(e.message))
  }, [])

  return (
    <main style={{ fontFamily: 'system-ui, sans-serif', padding: 24 }}>
      <h1>MeCloneMe — mini deploy</h1>
      <p><strong>API_BASE:</strong> {API_BASE || '(puste)'}</p>
      <p><strong>Health:</strong> {error ? `ERR: ${error}` : ping}</p>
      <p style={{opacity:.7,marginTop:24}}>
        Jeśli widzisz pusty ekran, otwórz DevTools → Console i wyślij screen.
      </p>
    </main>
  )
}