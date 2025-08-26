import asyncio, json, time, os, base64, hashlib, secrets
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException, Header
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError

# -------------------- helpers: base64url --------------------
def b64u_decode(s: str) -> bytes:
    s += "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s.encode())

def b64u_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")

# -------------------- env --------------------
NONCE_TTL   = int(os.getenv("NONCE_TTL", "300"))       # s
RATE_MAX    = int(os.getenv("RATE_MAX", "30"))         # req
RATE_WINDOW = int(os.getenv("RATE_WINDOW", "10"))      # s
SESSION_TTL = int(os.getenv("SESSION_TTL", "900"))     # s

# -------------------- stores (in-mem, demo) --------------------
NONCES: Dict[str, int] = {}                      # nonce -> exp
PUBKEYS: Dict[str, str] = {}                     # kid -> base64url(32B)
SESSIONS: Dict[str, Dict[str, Any]] = {}         # sid -> {kid, exp}
RATE: Dict[str, List[int]] = {}                  # ip -> [ts, ts, ...]

# -------------------- WS manager --------------------
class WSManager:
    def __init__(self):
        self.active: List[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    async def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, data: Dict[str, Any]):
        text = json.dumps(data)
        stale = []
        for ws in list(self.active):
            try:
                await ws.send_text(text)
            except Exception:
                stale.append(ws)
        for ws in stale:
            await self.disconnect(ws)

ws_manager = WSManager()

# -------------------- FastAPI --------------------
app = FastAPI(title="MeCloneMe API (mini)")

# -------------------- tiny utils --------------------
def now_i() -> int:
    return int(time.time())

def get_ip(req: Request) -> str:
    xf = req.headers.get("x-forwarded-for")
    if xf:
        return xf.split(",")[0].strip()
    return req.client.host if req.client else "0.0.0.0"

def require_bearer(authorization: Optional[str]) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer")
    return authorization.split(" ", 1)[1].strip()

# -------------------- models --------------------
class ChallengeResp(BaseModel):
    nonce: str
    aud: str
    ts: int

class VerifyReq(BaseModel):
    jws: str

class PubReq(BaseModel):
    kid: str
    pub: str   # base64url(32B ed25519)

# -------------------- HTML: mini panel --------------------
PANEL_HTML = """<!doctype html><meta charset="utf-8"><title>Guardian — mini panel</title>
<body style="font-family:system-ui, -apple-system, sans-serif; margin:24px">
<h1>Guardian — mini panel</h1>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
  <div style="border:1px solid #eee;border-radius:12px;padding:12px">
    <h3>Challenge</h3>
    <pre id="challenge" style="background:#f7f7f7;border:1px solid #eee;border-radius:8px;padding:12px;height:220px;overflow:auto">...</pre>
  </div>
  <div style="border:1px solid #eee;border-radius:12px;padding:12px">
    <h3>Live log <span id="wsst">WS: connecting...</span></h3>
    <pre id="log" style="background:#f7f7f7;border:1px solid #eee;border-radius:8px;padding:12px;height:220px;overflow:auto"></pre>
  </div>
</div>

<h3 style="margin-top:24px">Postęp projektu (tylko lokalnie — zapis w przeglądarce)</h3>
<div id="board"></div>
<button id="save">💾 Zapisz</button>
<button id="reset">↺ Reset</button>

<script>
const fmt = x => JSON.stringify(x, null, 2);
const challEl = document.getElementById('challenge');
const logEl = document.getElementById('log');
const stEl = document.getElementById('wsst');

function appendLog(obj){
  logEl.textContent += JSON.stringify(obj)+'\\n';
  logEl.scrollTop = logEl.scrollHeight;
}

fetch('/auth/challenge').then(r=>r.json()).then(j=>{
  challEl.textContent = fmt(j);
  window.__nonce = j.nonce;
}).catch(()=> challEl.textContent='API offline');

try{
  const ws = new WebSocket((location.protocol==='https:'?'wss':'ws')+'://'+location.host+'/shadow/ws');
  ws.onopen = ()=>{ stEl.textContent='WS: connected'; stEl.style.color='green'; appendLog({ts:Date.now()/1000|0, vec:{sys:'ws-connected'}}); };
  ws.onmessage = ev=>{ try{ appendLog(JSON.parse(ev.data)); }catch{} };
  ws.onerror = ()=>{ stEl.textContent='WS: error'; stEl.style.color='crimson'; };
  ws.onclose = ()=>{ stEl.textContent='WS: closed'; stEl.style.color='gray'; };
}catch{}

const lanes = [
  'Guardian/Auth',
  'AR Engine (R&D)',
  'App Shell / UI',
  'Cloud & Deploy',
  'MVP (całość)'
];

const board = document.getElementById('board');
const key='progress_bar_v1';
function render(){
  const saved = JSON.parse(localStorage.getItem(key)||'{}');
  board.innerHTML = lanes.map((name,i)=>{
    const v = Number(saved[i]||0);
    return `
      <div style="display:grid;grid-template-columns:220px 1fr 40px 40px;gap:8px;align-items:center;margin:8px 0">
        <div>${name}</div>
        <div style="height:8px;background:#eee;border-radius:6px;overflow:hidden">
          <div style="height:8px;background:#27ae60;width:${v}%"></div>
        </div>
        <input id="in${i}" value="${v}" style="width:40px">
        <div>${v}%</div>
      </div>`;
  }).join('');
}
render();

document.getElementById('save').onclick=()=>{
  const out={}; lanes.forEach((_,i)=> out[i]= Number(document.getElementById('in'+i).value||0));
  localStorage.setItem(key, JSON.stringify(out)); render();
};
document.getElementById('reset').onclick=()=>{ localStorage.removeItem(key); render(); };
</script>
</body>
"""

# -------------------- HTML: mobile signer --------------------
MOBILE_HTML = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Guardian — Mobile Signer</title>
<style>
  body{font-family:system-ui,ui-sans-serif;margin:16px;line-height:1.35}
  h1{font-size:22px;margin:0 0 12px}
  .row{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:12px}
  .card{border:1px solid #e5e5e5;border-radius:12px;padding:12px}
  textarea,input,button{width:100%;padding:8px;border-radius:8px;border:1px solid #ddd}
  pre{background:#f8f8f8;border:1px solid #eee;border-radius:8px;padding:10px;white-space:pre-wrap}
  .btn{cursor:pointer}
</style>
<h1>Guardian — Mobile Signer</h1>

<div class="row">
  <div class="card">
    <b>1) Klucz prywatny (PRIV, seed 32B)</b>
    <input id="kid" placeholder="dev-key-1" style="margin:8px 0">
    <textarea id="priv" rows="3" placeholder="Wklej PRIV z terminala"></textarea>
    <div style="color:#666;margin:8px 0">PRIV to seed 32B w base64url (z terminala). Strona zapisuje go lokalnie w przeglądarce.</div>
    <button id="save" class="btn">💾 Zapisz w przeglądarce</button>
  </div>

  <div class="card">
    <b>2) Rejestracja PUB</b>
    <button id="register" class="btn">🧾 Zarejestruj PUB na serwerze</button>
    <pre id="regOut"></pre>
  </div>
</div>

<div class="row">
  <div class="card">
    <b>3) Challenge</b>
    <button id="getCh" class="btn">🎯 Pobierz /auth/challenge</button>
    <pre id="chOut"></pre>
  </div>

  <div class="card">
    <b>4) Podpisz JWS i zweryfikuj</b>
    <button id="verify" class="btn">🔏 Podpisz & /guardian/verify</button>
    <pre id="verOut"></pre>
  </div>
</div>

<div class="row">
  <div class="card">
    <b>5) Token (Bearer)</b>
    <div style="display:flex;gap:8px;margin:8px 0">
      <button id="ping" class="btn">🔒 Ping /protected/hello</button>
      <button id="refresh" class="btn">🔄 Refresh</button>
      <button id="logout" class="btn">🚪 Logout</button>
    </div>
    <div>Wygasa za: <span id="ttl">-s</span></div>
    <pre id="tokOut"></pre>
  </div>
  <div></div>
</div>

<script src="https://cdn.jsdelivr.net/npm/tweetnacl@1.0.3/nacl.min.js"></script>
<script>
const enc = new TextEncoder();

const LSK='guardian_priv_seed';
const kidEl = document.getElementById('kid');
const privEl = document.getElementById('priv');
document.getElementById('save').onclick=()=>{ localStorage.setItem(LSK, privEl.value.trim()); alert('Zapisano PRIV'); };
privEl.value = localStorage.getItem(LSK) || '';

const regOut = document.getElementById('regOut');
const chOut = document.getElementById('chOut');
const verOut = document.getElementById('verOut');
const tokOut = document.getElementById('tokOut');
const ttlEl = document.getElementById('ttl');

let lastChallenge=null;
let sessionSid=null;
let sessionExp=0;
let tmr=null;

function b64u(b){ return btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\\+/g,'-').replace(/\\//g,'_').replace(/=+$/,''); }
function fromB64u(s){ s = s.replace(/-/g,'+').replace(/_/g,'/'); while(s.length%4) s+='='; const bin = atob(s); return new Uint8Array([...bin].map(c=>c.charCodeAt(0))); }

function getKeypair(){
  const seed = privEl.value.trim();
  if(!seed) throw new Error('Brak PRIV (seed 32B b64url)');
  const seedBytes = fromB64u(seed);
  if(seedBytes.length !== 32) throw new Error('PRIV musi być 32B');
  return nacl.sign.keyPair.fromSeed(seedBytes);
}

document.getElementById('register').onclick = async ()=>{
  try{
    const kp = getKeypair();
    const pub64 = b64u(kp.publicKey);
    const kid = kidEl.value.trim() || 'dev-key-1';
    const r = await fetch('/admin/register_pubkey', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({kid:kid, pub:pub64})});
    regOut.textContent = await r.text();
  }catch(e){ regOut.textContent = 'ERR: '+e.message; }
};

document.getElementById('getCh').onclick = async ()=>{
  const r = await fetch('/auth/challenge');
  lastChallenge = await r.json();
  chOut.textContent = JSON.stringify(lastChallenge, null, 2);
};

function startClock(){
  clearInterval(tmr);
  tmr = setInterval(()=>{
    const left = Math.max(0, sessionExp - Math.floor(Date.now()/1000));
    ttlEl.textContent = left+'s';
  }, 1000);
}

document.getElementById('verify').onclick = async ()=>{
  try{
    if(!lastChallenge) throw new Error('Najpierw pobierz challenge.');
    const kid = kidEl.value.trim() || 'dev-key-1';
    const hdr = {alg:"EdDSA", typ:"JWT", kid};
    const pld = {aud:lastChallenge.aud, nonce:lastChallenge.nonce, ts: Math.floor(Date.now()/1000)};
    const h_b = b64u(enc.encode(JSON.stringify(hdr)));
    const p_b = b64u(enc.encode(JSON.stringify(pld)));
    const msg = enc.encode(h_b+'.'+p_b);
    const kp = getKeypair();
    const sig = nacl.sign.detached(msg, kp.secretKey);
    const jws = h_b+'.'+p_b+'.'+b64u(sig);
    const r = await fetch('/guardian/verify', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({jws})});
    const j = await r.json();
    verOut.textContent = JSON.stringify(j, null, 2);
    if(j.ok && j.session){
      sessionSid = j.session; sessionExp = j.exp; startClock();
      tokOut.textContent = JSON.stringify({ok:true}, null, 2);
    }
  }catch(e){ verOut.textContent = 'ERR: '+e.message; }
};

async function withBearer(path, method='GET'){
  if(!sessionSid) throw new Error('Brak aktywnej sesji.');
  const r = await fetch(path, {method, headers:{'Authorization':'Bearer '+sessionSid}});
  return r.json();
}

document.getElementById('ping').onclick = async ()=>{
  try{ tokOut.textContent = JSON.stringify(await withBearer('/protected/hello'), null, 2); }catch(e){ tokOut.textContent='ERR: '+e.message; }
};
document.getElementById('refresh').onclick = async ()=>{
  try{
    const j = await withBearer('/auth/refresh', 'POST');
    if(j.ok){ sessionExp = j.exp; startClock(); }
    tokOut.textContent = JSON.stringify(j, null, 2);
  }catch(e){ tokOut.textContent='ERR: '+e.message; }
};
document.getElementById('logout').onclick = async ()=>{
  try{
    const j = await withBearer('/auth/logout', 'POST');
    sessionSid=null; sessionExp=0; startClock();
    tokOut.textContent = JSON.stringify(j, null, 2);
  }catch(e){ tokOut.textContent='ERR: '+e.message; }
};
</script>
"""

# -------------------- routes: UI --------------------
@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(PANEL_HTML)

@app.get("/mobile", response_class=HTMLResponse)
def mobile_page():
    return HTMLResponse(MOBILE_HTML)

# -------------------- routes: WS test bus --------------------
@app.websocket("/shadow/ws")
async def shadow_ws(ws: WebSocket):
    await ws_manager.connect(ws)
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
    await ws_manager.broadcast(frame.dict())
    return {"ok": True}

# -------------------- routes: admin --------------------
@app.post("/admin/register_pubkey")
def register_pubkey(req: PubReq):
    try:
        if len(b64u_decode(req.pub)) != 32:
            return {"ok": False, "reason": "bad-pubkey"}
    except Exception:
        return {"ok": False, "reason": "bad-pubkey"}
    PUBKEYS[req.kid] = req.pub
    return {"ok": True, "registered": list(PUBKEYS.keys())}

# -------------------- routes: challenge --------------------
@app.get("/auth/challenge", response_model=ChallengeResp)
def challenge(aud: str = "mobile"):
    now = now_i()
    # sprzątaj stare
    for n, exp in list(NONCES.items()):
        if exp < now: NONCES.pop(n, None)
    nonce = secrets.token_hex(16)
    NONCES[nonce] = now + NONCE_TTL
    return {"nonce": nonce, "aud": aud, "ts": now}

# -------------------- routes: verify JWS --------------------
def rate_limit(req: Request):
    ip = get_ip(req)
    now = now_i()
    buf = RATE.setdefault(ip, [])
    # purge
    while buf and buf[0] <= now - RATE_WINDOW:
        buf.pop(0)
    if len(buf) >= RATE_MAX:
        raise HTTPException(429, "rate-limit")
    buf.append(now)

@app.post("/guardian/verify")
async def guardian_verify(req: Request, body: VerifyReq):
    rate_limit(req)

    # JWS: header.payload.signature (base64url), EdDSA/Ed25519
    try:
        parts = body.jws.split(".")
        if len(parts) != 3:
            return {"ok": False, "reason": "bad-format"}
        h_b, p_b, s_b = parts
        header = json.loads(b64u_decode(h_b))
        payload = json.loads(b64u_decode(p_b))
        sig = b64u_decode(s_b)

        if header.get("alg") != "EdDSA":
            return {"ok": False, "reason": "alg-not-supported"}

        kid = header.get("kid")
        if not kid or kid not in PUBKEYS:
            return {"ok": False, "reason": "unknown-kid"}

        verify_key = VerifyKey(b64u_decode(PUBKEYS[kid]))
        signed = (h_b + "." + p_b).encode()
        try:
            verify_key.verify(signed, sig)
        except BadSignatureError:
            return {"ok": False, "reason": "bad-signature"}

        # claims
        now = now_i()
        if abs(now - int(payload.get("ts", 0))) > NONCE_TTL:
            return {"ok": False, "reason": "nonce-expired"}
        aud = payload.get("aud"); nonce = payload.get("nonce")
        if not aud or not nonce:
            return {"ok": False, "reason": "missing-claims"}

        exp = NONCES.get(nonce)
        if not exp or exp < now:
            return {"ok": False, "reason": "nonce-expired"}

        # consume nonce
        NONCES.pop(nonce, None)

        # create session
        sid = "sess_" + secrets.token_hex(16)
        sess_exp = now + SESSION_TTL
        SESSIONS[sid] = {"kid": kid, "exp": sess_exp}

        frame = {"ts": now, "kid": kid, "vec": {"auth": "ok", "aud": aud}}
        asyncio.create_task(ws_manager.broadcast(frame))
        return {"ok": True, "payload": payload, "session": sid, "exp": sess_exp}
    except Exception as e:
        return {"ok": False, "reason": "server-error", "detail": str(e)}

# -------------------- session helpers / protected --------------------
def get_session(sid: str) -> Dict[str, Any]:
    s = SESSIONS.get(sid)
    if not s:
        raise HTTPException(401, "bad-session")
    if s["exp"] < now_i():
        SESSIONS.pop(sid, None)
        raise HTTPException(401, "expired")
    return s

@app.get("/protected/hello")
def protected_hello(authorization: Optional[str] = Header(None)):
    sid = require_bearer(authorization)
    s = get_session(sid)
    return {"ok": True, "msg": "hello dev-user", "kid": s["kid"], "exp": s["exp"]}

@app.post("/auth/refresh")
def refresh(authorization: Optional[str] = Header(None)):
    sid = require_bearer(authorization)
    s = get_session(sid)
    s["exp"] = now_i() + SESSION_TTL
    return {"ok": True, "exp": s["exp"]}

@app.post("/auth/logout")
def logout(authorization: Optional[str] = Header(None)):
    sid = require_bearer(authorization)
    SESSIONS.pop(sid, None)
    return {"ok": True}

