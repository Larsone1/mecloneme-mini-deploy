# backend/app.py
import asyncio, json, time, os, base64, hashlib
from typing import List, Dict, Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Header
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ---- base64url helpers -------------------------------------------------------
def b64u_decode(s: str) -> bytes:
    pad = "=" * ((4 - len(s) % 4) % 4)
    return base64.urlsafe_b64decode((s + pad).encode())

def b64u_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")

# ---- Ed25519 (PyNaCl) --------------------------------------------------------
from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError

# ---- proste pamięci w RAM ----------------------------------------------------
NONCES: Dict[str, int] = {}
PUBKEYS: Dict[str, str] = {}          # kid -> base64url(pub32B)
SESSIONS: Dict[str, Dict[str, Any]] = {}  # token -> {kid, user, exp}

NONCE_TTL = int(os.getenv("NONCE_TTL", "300"))      # 5 min
SESSION_TTL = int(os.getenv("SESSION_TTL", "900"))  # 15 min

# ---- WebSocket manager (live log) -------------------------------------------
class WSManager:
    def __init__(self) -> None:
        self.active: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    async def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, data: Dict[str, Any]):
        dead = []
        text = json.dumps(data)
        for ws in list(self.active):
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(ws)

ws_manager = WSManager()

# ---- FastAPI app & CORS ------------------------------------------------------
app = FastAPI(title="MeCloneMe API (mini)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

# ---- Mini panel (dashboard) --------------------------------------------------
PANEL_HTML = """<!doctype html><meta charset="utf-8"><title>Guardian – mini panel</title>
<body style="font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; padding:16px">
<h1>Guardian — mini panel</h1>

<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
  <div style="border:1px solid #eee;border-radius:12px;padding:12px">
    <h2>Challenge</h2>
    <pre id="challenge" style="background:#f7f7f7;border:1px solid #eee;border-radius:8px;padding:12px">...</pre>
  </div>

  <div style="border:1px solid #eee;border-radius:12px;padding:12px">
    <h2 style="display:flex;align-items:center;gap:10px">
      Live log
      <small id="wsStatus" style="font-weight:600;color:#a00">WS: disconnected</small>
    </h2>
    <pre id="log" style="background:#f7f7f7;border:1px solid #eee;border-radius:8px;padding:12px;height:260px;overflow:auto"></pre>
  </div>

  <div style="grid-column:1 / span 2;border:1px solid #eee;border-radius:12px;padding:12px">
    <h2>Postęp projektu (tylko lokalnie – zapis w przeglądarce)</h2>
    <div id="bars" style="display:grid;gap:12px">
      <div class="pr"><div style="min-width:160px">Guardian/Auth</div><div class="meter"><div id="bar-auth" class="fill"></div></div><input id="num-auth" data-k="auth" class="progress-num" type="number" min="0" max="100" value="0" style="width:64px"><div id="label-auth" style="width:44px;text-align:right">0%</div></div>
      <div class="pr"><div style="min-width:160px">AR Engine (R&amp;D)</div><div class="meter"><div id="bar-ar" class="fill"></div></div><input id="num-ar" data-k="ar" class="progress-num" type="number" min="0" max="100" value="0" style="width:64px"><div id="label-ar" style="width:44px;text-align:right">0%</div></div>
      <div class="pr"><div style="min-width:160px">App Shell / UI</div><div class="meter"><div id="bar-ui" class="fill"></div></div><input id="num-ui" data-k="ui" class="progress-num" type="number" min="0" max="100" value="0" style="width:64px"><div id="label-ui" style="width:44px;text-align:right">0%</div></div>
      <div class="pr"><div style="min-width:160px">Cloud &amp; Deploy</div><div class="meter"><div id="bar-infra" class="fill"></div></div><input id="num-infra" data-k="infra" class="progress-num" type="number" min="0" max="100" value="0" style="width:64px"><div id="label-infra" style="width:44px;text-align:right">0%</div></div>
      <div class="pr"><div style="min-width:160px">MVP (całość)</div><div class="meter"><div id="bar-mvp" class="fill"></div></div><input id="num-mvp" data-k="mvp" class="progress-num" type="number" min="0" max="100" value="5" style="width:64px"><div id="label-mvp" style="width:44px;text-align:right">5%</div></div>
    </div>
    <div style="margin-top:12px;display:flex;gap:8px">
      <button id="saveAll">💾 Zapisz</button>
      <button id="resetAll">↺ Reset</button>
    </div>
  </div>
</div>

<style>
  .pr{display:grid;grid-template-columns:160px 1fr 64px 44px;gap:8px;align-items:center}
  .meter{height:14px;background:#eee;border-radius:10px;overflow:hidden;box-shadow:inset 0 0 0 1px #e0e0e0}
  .fill{height:100%;width:0%;background:linear-gradient(90deg,#7dd3fc,#34d399);transition:width .25s ease}
  button{padding:6px 10px;border:1px solid #ddd;border-radius:8px;background:#fff;cursor:pointer}
  button:hover{background:#f7f7f7}
</style>

<script>
(async function(){
  const log = document.getElementById('log');
  const s   = document.getElementById('wsStatus');

  function append(m){ log.textContent += JSON.stringify(m)+'\\n'; log.scrollTop = log.scrollHeight; }

  try{
    const r = await fetch('/auth/challenge');
    const x = await r.json();
    document.getElementById('challenge').textContent = JSON.stringify(x,null,2);
  }catch(e){
    document.getElementById('challenge').textContent = 'API offline';
  }

  const url = (location.protocol==='https:'?'wss':'ws')+'://'+location.host+'/shadow/ws';
  const ws = new WebSocket(url);
  ws.onopen    = ()=>{ s.textContent='WS: connected'; s.style.color='#0a0'; };
  ws.onclose   = ()=>{ s.textContent='WS: disconnected'; s.style.color='#a00'; };
  ws.onerror   = ()=>{ s.textContent='WS: error'; s.style.color='#a00'; };
  ws.onmessage = (e)=>{ try{ const m = JSON.parse(e.data); if(m.vec) append(m); }catch{} };

  // progress meters (local)
  const KEYS=['auth','ar','ui','infra','mvp'];
  function setVal(k,val,save=true){
    val=Math.max(0,Math.min(100,parseInt(val||0,10)));
    document.getElementById('bar-'+k).style.width=val+'%';
    document.getElementById('num-'+k).value=val;
    document.getElementById('label-'+k).textContent=val+'%';
    if(save) localStorage.setItem('p_'+k,String(val));
  }
  function load(){ KEYS.forEach(k=>{ const v=localStorage.getItem('p_'+k); setVal(k, v==null?(k==='mvp'?5:0):v, false); }); }
  document.addEventListener('input',(ev)=>{
    if(ev.target.classList.contains('progress-num')) setVal(ev.target.dataset.k, ev.target.value);
  });
  document.getElementById('saveAll').onclick = ()=>{ KEYS.forEach(k=>setVal(k, document.getElementById('num-'+k).value)); alert('Zapisano lokalnie ✅'); };
  document.getElementById('resetAll').onclick= ()=>{ KEYS.forEach(k=>localStorage.removeItem('p_'+k)); load(); };
  load();
})();
</script>
</body>"""

@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(PANEL_HTML)

# ---- Challenge ---------------------------------------------------------------
class ChallengeResp(BaseModel):
    nonce: str
    aud: str
    ts: int

@app.get("/auth/challenge", response_model=ChallengeResp)
def challenge(aud: str = "mobile"):
    now = int(time.time())
    # czyść stare
    for n, exp in list(NONCES.items()):
        if exp < now:
            NONCES.pop(n, None)
    # wygeneruj nowy
    nonce = os.urandom(16).hex()
    NONCES[nonce] = now + NONCE_TTL
    return {"nonce": nonce, "aud": aud, "ts": now}

# ---- Verify JWS + wystawienie session token ---------------------------------
class VerifyReq(BaseModel):
    jws: str
    kid: Optional[str] = None  # opcjonalnie – zwykle w headerze

@app.post("/guardian/verify")
async def guardian_verify(req: VerifyReq):
    """
    Oczekujemy compact JWS: header.payload.signature (base64url),
    gdzie alg=EdDSA oraz podpis Ed25519 (32B pubkey zarejestrowany wcześniej).
    Po sukcesie tworzymy krótko-żyjącą sesję (Bearer).
    """
    try:
        parts = req.jws.split(".")
        if len(parts) != 3:
            return {"ok": False, "reason": "bad-format"}

        h_b, p_b, s_b = parts
        header  = json.loads(b64u_decode(h_b))
        payload = json.loads(b64u_decode(p_b))
        sig     = b64u_decode(s_b)

        if header.get("alg") != "EdDSA":
            return {"ok": False, "reason": "alg-not-supported"}

        kid = header.get("kid") or req.kid
        if not kid or kid not in PUBKEYS:
            return {"ok": False, "reason": "unknown-kid"}

        # weryfikacja podpisu
        signed = (h_b + "." + p_b).encode()
        try:
            verify_key = VerifyKey(b64u_decode(PUBKEYS[kid]))
            verify_key.verify(signed, sig)
        except BadSignatureError:
            return {"ok": False, "reason": "bad-signature"}

        # proste sprawdzenia aplikacyjne
        now = int(time.time())
        ts = int(payload.get("ts", 0))
        if abs(now - ts) > NONCE_TTL:
            return {"ok": False, "reason": "nonce-expired"}

        aud   = payload.get("aud")
        nonce = payload.get("nonce")
        if not aud or not nonce:
            return {"ok": False, "reason": "missing-claims"}

        exp = NONCES.get(nonce)
        if not exp or exp < now:
            return {"ok": False, "reason": "nonce-expired"}

        # nonce zużyty
        NONCES.pop(nonce, None)

        # sukces → wystaw sesję (tu user=dev demo; docelowo mapowanie kid→user)
        sess_token = "sess_" + os.urandom(18).hex()
        sess_exp   = now + SESSION_TTL
        SESSIONS[sess_token] = {"kid": kid, "user": "dev-user", "exp": sess_exp}

        frame = {"ts": now, "kid": kid, "vec": {"auth": "ok", "aud": aud}}
        asyncio.create_task(ws_manager.broadcast(frame))
        return {"ok": True, "payload": payload, "session": sess_token, "exp": sess_exp}

    except Exception as e:
        return {"ok": False, "reason": "server-error", "detail": str(e)}

# ---- Admin: rejestracja publicznego klucza (demo) ----------------------------
class PubReq(BaseModel):
    kid: str
    pub: str  # base64url 32B (Ed25519)

@app.post("/admin/register_pubkey")
def register_pubkey(req: PubReq):
    if not req.kid or not req.pub:
        return {"ok": False}
    # minimalna walidacja długości
    try:
        if len(b64u_decode(req.pub)) != 32:
            return {"ok": False, "reason": "bad-pubkey"}
    except Exception:
        return {"ok": False, "reason": "bad-pubkey"}
    PUBKEYS[req.kid] = req.pub
    return {"ok": True, "registered": list(PUBKEYS.keys())}

# ---- WebSockets + shadow ingest ----------------------------------------------
@app.websocket("/shadow/ws")
async def ws_shadow(ws: WebSocket):
    await ws_manager.connect(ws)
    # hello – żeby od razu było coś w logu
    await ws.send_text(json.dumps({"ts": int(time.time()), "vec": {"sys": "ws-connected"}}))
    try:
        while True:
            await ws.receive_text()  # nic nie robimy z wejściem
    except WebSocketDisconnect:
        await ws_manager.disconnect(ws)

class ShadowFrame(BaseModel):
    ts: int
    kid: Optional[str] = None
    vec: Dict[str, Any] = {}

@app.post("/shadow/ingest")
async def shadow_ingest(frame: ShadowFrame):
    # proste logowanie do pliku + broadcast
    os.makedirs("logs", exist_ok=True)
    with open("logs/shadow.jsonl", "a") as f:
        f.write(json.dumps(frame.dict()) + "\n")
    await ws_manager.broadcast(frame.dict())
    return {"ok": True}

# ---- Prosty protected endpoint (wymaga Bearer) --------------------------------
def require_session(auth_header: Optional[str]) -> Dict[str, Any]:
    if not auth_header or not auth_header.startswith("Bearer "):
        return {}
    token = auth_header.split(" ", 1)[1].strip()
    s = SESSIONS.get(token)
    if not s:
        return {}
    if s["exp"] < int(time.time()):
        SESSIONS.pop(token, None)
        return {}
    return {"token": token, **s}

@app.get("/protected/hello")
def protected_hello(authorization: Optional[str] = Header(None)):
    s = require_session(authorization)
    if not s:
        return JSONResponse({"ok": False, "reason": "unauthorized"}, status_code=401)
    return {"ok": True, "msg": f"hello {s['user']}", "kid": s["kid"], "exp": s["exp"]}

# ---- Mobilny panel demo (rejestracja + podpis + verify + token + ping) --------
@app.get("/mobile")
def mobile_page():
    return HTMLResponse("""
<!doctype html><meta charset="utf-8"><title>Guardian – Mobile Signer</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  body{font-family:ui-sans-serif,system-ui;margin:16px;line-height:1.4}
  h1{font-size:20px;margin:0 0 12px}
  .row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
  .card{border:1px solid #eee;border-radius:12px;padding:12px}
  textarea,input,button{width:100%;padding:10px;border:1px solid #ddd;border-radius:8px}
  pre{background:#f7f7f7;border:1px solid #eee;border-radius:8px;padding:8px;white-space:pre-wrap}
  button{cursor:pointer}
</style>
<h1>Guardian — Mobile Signer</h1>

<div class="row">
  <div class="card">
    <b>1) Klucz prywatny (PRIV, seed 32B)</b>
    <input id="kid" placeholder="kid" value="dev-key-1" style="margin:8px 0">
    <textarea id="priv" rows="3" placeholder="Wklej PRIV z terminala (base64url)"></textarea>
    <div class="muted">PRIV to seed 32B w base64url (z terminala). Strona zapisuje go lokalnie w przeglądarce.</div>
    <button id="save" style="margin-top:8px">💾 Zapisz w przeglądarce</button>
  </div>

  <div class="card">
    <b>2) Rejestracja PUB</b>
    <button id="register">📡 Zarejestruj PUB na serwerze</button>
    <pre id="regOut"></pre>
  </div>

  <div class="card">
    <b>3) Challenge</b>
    <button id="getCh">🎯 Pobierz /auth/challenge</button>
    <pre id="chOut"></pre>
  </div>

  <div class="card">
    <b>4) Podpisz JWS i zweryfikuj</b>
    <button id="verify">🔐 Podpisz &amp; /guardian/verify</button>
    <pre id="verOut"></pre>
  </div>

  <div class="card">
    <b>5) Token (Bearer)</b>
    <input id="tok" placeholder="session token (auto)" readonly>
    <button id="ping" style="margin-top:8px">🔒 Ping /protected/hello</button>
    <pre id="pingOut"></pre>
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/tweetnacl@1.0.3/nacl.min.js"></script>
<script>
const enc=new TextEncoder(), dec=new TextDecoder();
const b64u = b=> btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\\+/g,'-').replace(/\\//g,'_').replace(/=+$/,'');
const fromB64u = s => { s=s.replace(/-/g,'+').replace(/_/g,'/'); while(s.length%4) s+='='; const bin=atob(s), out=new Uint8Array(bin.length); for(let i=0;i<bin.length;i++) out[i]=bin.charCodeAt(i); return out; };

const LS_KEY="guardian_priv_seed";
const $ = id => document.getElementById(id);
$("priv").value = localStorage.getItem(LS_KEY) || "";

$("save").onclick = ()=>{ localStorage.setItem(LS_KEY, $("priv").value.trim()); alert("Zapisano PRIV ✅"); };

function getKeypair(){
  const seedB64u = $("priv").value.trim();
  if(!seedB64u) throw new Error("Brak PRIV");
  const seed = fromB64u(seedB64u);
  if(seed.length !== 32) throw new Error("PRIV musi mieć 32B (base64url)");
  const kp = nacl.sign.keyPair.fromSeed(seed);
  return {publicKey: kp.publicKey, secretKey: kp.secretKey};
}

$("register").onclick = async ()=>{
  try{
    const kp = getKeypair();
    const pub = b64u(kp.publicKey);
    const kid = $("kid").value.trim() || "dev-key-1";
    const r = await fetch("/admin/register_pubkey", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({kid, pub})});
    $("regOut").textContent = await r.text();
  }catch(e){ $("regOut").textContent = "ERR: " + e.message; }
};

let lastChallenge = null;

$("getCh").onclick = async ()=>{
  const r = await fetch("/auth/challenge");
  lastChallenge = await r.json();
  $("chOut").textContent = JSON.stringify(lastChallenge,null,2);
};

$("verify").onclick = async ()=>{
  try{
    if(!lastChallenge) throw new Error("Najpierw pobierz challenge.");
    const kid = $("kid").value.trim() || "dev-key-1";
    const hdr = {alg:"EdDSA",typ:"JWT",kid};
    const pld = {aud:lastChallenge.aud, nonce:lastChallenge.nonce, ts: Math.floor(Date.now()/1000)};

    const h_b = b64u(enc.encode(JSON.stringify(hdr)));
    const p_b = b64u(enc.encode(JSON.stringify(pld)));
    const msg = enc.encode(h_b+"."+p_b);

    const kp = getKeypair();
    const sig = nacl.sign.detached(msg, kp.secretKey);
    const jws = h_b+"."+p_b+"."+b64u(sig);

    const r = await fetch("/guardian/verify", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({jws})});
    const t = await r.json();
    $("verOut").textContent = JSON.stringify(t,null,2);
    if(t.ok && t.session){ $("tok").value = t.session; }
  }catch(e){
    $("verOut").textContent = "ERR: " + e.message;
  }
};

$("ping").onclick = async ()=>{
  try{
    const tok = $("tok").value.trim();
    const r = await fetch("/protected/hello", {headers: {"Authorization":"Bearer "+tok}});
    $("pingOut").textContent = await r.text();
  }catch(e){
    $("pingOut").textContent = "ERR: " + e.message;
  }
};
</script>
""")
