# backend/app.py
# MeCloneMe mini backend — Guardian + WS + sesje Bearer + rate limit (in-memory)

import os, time, json, base64, asyncio, secrets
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Header
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel
from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError

# ---------- utils: base64url ----------
def b64u_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

def b64u_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode((s + pad).encode())

now_s = lambda: int(time.time())

# ---------- config from env ----------
NONCE_TTL   = int(os.getenv("NONCE_TTL", "300"))     # 5 min
SESSION_TTL = int(os.getenv("SESSION_TTL", "900"))   # 15 min
RATE_MAX    = int(os.getenv("RATE_MAX", "30"))       # max req / window
RATE_WINDOW = int(os.getenv("RATE_WINDOW", "10"))    # sek

# ---------- in-memory stores ----------
PUBKEYS: Dict[str, str] = {}              # kid -> pub (base64url, 32B Ed25519)
NONCES: Dict[str, int] = {}               # nonce -> exp ts
SESSIONS: Dict[str, Dict[str, Any]] = {}  # sid -> {"kid":..., "exp": int}
RATELIM: Dict[str, List[int]] = {}        # bucket -> [ts,...]

def rate_allow(bucket: str) -> bool:
    t = now_s()
    L = RATELIM.setdefault(bucket, [])
    # drop out-of-window
    cut = t - RATE_WINDOW
    i = 0
    for j in range(len(L)):
        if L[j] >= cut:
            i = j; break
    else:
        i = len(L)
    if i > 0:
        del L[:i]
    if len(L) >= RATE_MAX:
        return False
    L.append(t)
    return True

# ---------- WS manager ----------
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
        msg = json.dumps(data)
        stale = []
        for ws in list(self.active):
            try:
                await ws.send_text(msg)
            except Exception:
                stale.append(ws)
        for ws in stale:
            await self.disconnect(ws)

ws_manager = WSManager()

# ---------- FastAPI ----------
app = FastAPI(title="MeCloneMe API (mini)")

# ---------- Mini panel (/) ----------
PANEL_HTML = """<!doctype html>
<meta charset="utf-8"><title>Guardian — mini panel</title>
<body style="font-family: -apple-system, system-ui, sans-serif; padding:18px">
<h1>Guardian — mini panel</h1>

<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
  <div style="border:1px solid #eee;border-radius:12px;padding:12px">
    <h2>Challenge</h2>
    <pre id="challenge" style="background:#f7f7f7;padding:12px;border:1px solid #eee;border-radius:8px">...</pre>
  </div>
  <div style="border:1px solid #eee;border-radius:12px;padding:12px">
    <h2>Live log <span id="wsState" style="color:#0a0">WS: connected</span></h2>
    <pre id="log" style="background:#f7f7f7;padding:12px;border:1px solid #eee;border-radius:8px;height:260px;overflow:auto"></pre>
  </div>
</div>

<div style="margin-top:18px;border:1px solid #eee;border-radius:12px;padding:12px">
  <h2>Postęp projektu (tylko lokalnie — zapis w przeglądarce)</h2>
  <div id="bars"></div>
  <div style="margin-top:8px">
    <button id="save">💾 Zapisz</button>
    <button id="reset">♻️ Reset</button>
  </div>
</div>

<script>
const log = document.getElementById('log');
const ch = document.getElementById('challenge');
const wsState = document.getElementById('wsState');

fetch('/auth/challenge').then(r=>r.json()).then(x=>{
  ch.textContent = JSON.stringify(x,null,2);
}).catch(()=>{ ch.textContent = 'API offline'; });

function append(m){
  log.textContent += m + "\\n";
  log.scrollTop = log.scrollHeight;
}
try{
  const ws = new WebSocket((location.protocol==='https:'?'wss':'ws')+'://'+location.host+'/shadow/ws');
  ws.onopen = ()=> wsState.textContent='WS: connected';
  ws.onclose= ()=> wsState.textContent='WS: closed';
  ws.onmessage = (e)=> append(e.data);
}catch(e){ wsState.textContent='WS error'; }

const items = [
  'Guardian/Auth','AR Engine (R&D)','App Shell / UI','Cloud & Deploy','MVP (całość)'
];
const LS_KEY = 'progress_v1';
let state = JSON.parse(localStorage.getItem(LS_KEY)||'{}');
const bars = document.getElementById('bars');
function row(label,k){
  const wrap = document.createElement('div');
  wrap.style.display='grid'; wrap.style.gridTemplateColumns='160px 1fr 40px 40px'; wrap.style.gap='8px';
  const l=document.createElement('div'); l.textContent=label; wrap.appendChild(l);
  const bar=document.createElement('div');
  bar.style.height='8px'; bar.style.marginTop='8px'; bar.style.background='#eee'; bar.style.borderRadius='8px';
  const fill=document.createElement('div'); fill.style.height='8px'; fill.style.background='#3aa655'; fill.style.borderRadius='8px'; bar.appendChild(fill);
  const inp=document.createElement('input'); inp.type='number'; inp.min=0; inp.max=100; inp.value=state[k]||0;
  const pct=document.createElement('div'); pct.textContent=(state[k]||0)+'%';
  function sync(){ const v=Math.max(0,Math.min(100,parseInt(inp.value||'0'))); fill.style.width=v+'%'; pct.textContent=v+'%'; }
  inp.oninput=()=>{ state[k]=parseInt(inp.value||'0'); sync(); }
  wrap.appendChild(bar); wrap.appendChild(inp); wrap.appendChild(pct);
  bars.appendChild(wrap); sync();
}
items.forEach((name,i)=> row(name,'k'+i));
document.getElementById('save').onclick=()=>{ localStorage.setItem(LS_KEY, JSON.stringify(state)); alert('Zapisano w przeglądarce.'); }
document.getElementById('reset').onclick=()=>{ localStorage.removeItem(LS_KEY); location.reload(); }
</script>
</body>
"""
@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(PANEL_HTML)

# ---------- Challenge ----------
class ChallengeResp(BaseModel):
    nonce: str
    aud: str
    ts: int

@app.get("/auth/challenge", response_model=ChallengeResp)
def challenge(aud: str = "mobile", request: Request = None):
    # rate per IP
    ip = (request.client.host if request and request.client else "ip-unknown")
    if not rate_allow(f"ip:{ip}:challenge"):
        return JSONResponse({"ok": False, "reason": "rate-limit"}, status_code=429)

    t = now_s()
    # cleanup old nonces
    for n, exp in list(NONCES.items()):
        if exp < t: NONCES.pop(n, None)
    nonce = secrets.token_hex(16)
    NONCES[nonce] = t + NONCE_TTL
    return {"nonce": nonce, "aud": aud, "ts": t}

# ---------- JWS verify (Ed25519) ----------
class VerifyReq(BaseModel):
    jws: str

@app.post("/guardian/verify")
async def guardian_verify(req: VerifyReq, request: Request):
    # rate: per IP and optionally per kid after parse
    ip = (request.client.host if request and request.client else "ip-unknown")
    if not rate_allow(f"ip:{ip}:verify"):
        return JSONResponse({"ok": False, "reason": "rate-limit"}, status_code=429)

    try:
        parts = req.jws.split(".")
        if len(parts) != 3:
            return {"ok": False, "reason": "bad-format"}
        h_b, p_b, s_b = parts
        header = json.loads(b64u_decode(h_b))
        payload = json.loads(b64u_decode(p_b))
        sig = b64u_decode(s_b)

        if header.get("alg") != "EdDSA":
            return {"ok": False, "reason": "alg-not-supported"}

        kid = header.get("kid")
        if (not kid) or (kid not in PUBKEYS):
            return {"ok": False, "reason": "unknown-kid"}

        # second rate bucket per kid
        if not rate_allow(f"kid:{kid}:verify"):
            return JSONResponse({"ok": False, "reason": "rate-limit"}, status_code=429)

        verify_key = VerifyKey(b64u_decode(PUBKEYS[kid]))
        signed = (h_b + "." + p_b).encode()
        try:
            verify_key.verify(signed, sig)
        except BadSignatureError:
            return {"ok": False, "reason": "bad-signature"}

        # app checks
        t = now_s()
        if abs(t - int(payload.get("ts", 0))) > NONCE_TTL:
            return {"ok": False, "reason": "nonce-expired"}

        aud = payload.get("aud")
        nonce = payload.get("nonce")
        if (not aud) or (not nonce):
            return {"ok": False, "reason": "missing-claims"}

        exp = NONCES.get(nonce)
        if (not exp) or exp < t:
            return {"ok": False, "reason": "nonce-expired"}
        # consume nonce
        NONCES.pop(nonce, None)

        # create session
        sid = "sess_" + b64u_encode(os.urandom(16))
        sess_exp = t + SESSION_TTL
        SESSIONS[sid] = {"kid": kid, "exp": sess_exp}

        # live log
        frame = {"ts": t, "kid": kid, "vec": {"auth": "ok", "aud": aud}}
        asyncio.create_task(ws_manager.broadcast(frame))

        return {"ok": True, "payload": payload, "session": sid, "exp": sess_exp}
    except Exception as e:
        return {"ok": False, "reason": "server-error", "detail": str(e)}

# ---------- Admin: register pubkey ----------
class PubReq(BaseModel):
    kid: str
    pub: str  # base64url(32B Ed25519)

@app.post("/admin/register_pubkey")
def register_pubkey(req: PubReq):
    try:
        if len(b64u_decode(req.pub)) != 32:
            return {"ok": False, "reason": "bad-pubkey"}
    except Exception:
        return {"ok": False, "reason": "bad-pubkey"}
    PUBKEYS[req.kid] = req.pub
    return {"ok": True, "registered": list(PUBKEYS.keys())}

# ---------- Protected routes (Bearer) ----------
def get_session_from_auth(authorization: Optional[str]) -> Optional[Dict[str, Any]]:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    sid = authorization.split(" ", 1)[1].strip()
    ses = SESSIONS.get(sid)
    if not ses:
        return None
    if ses["exp"] < now_s():
        SESSIONS.pop(sid, None)
        return None
    # sliding window refresh in /protected/refresh
    ses["sid"] = sid
    return ses

@app.get("/protected/hello")
def protected_hello(Authorization: Optional[str] = Header(default=None)):
    ses = get_session_from_auth(Authorization)
    if not ses:
        return JSONResponse({"ok": False, "reason": "unauthorized"}, status_code=401)
    return {"ok": True, "kid": ses["kid"], "exp": ses["exp"]}

@app.post("/protected/refresh")
def protected_refresh(Authorization: Optional[str] = Header(default=None)):
    ses = get_session_from_auth(Authorization)
    if not ses:
        return JSONResponse({"ok": False, "reason": "unauthorized"}, status_code=401)
    ses["exp"] = now_s() + SESSION_TTL
    SESSIONS[ses["sid"]] = {"kid": ses["kid"], "exp": ses["exp"]}
    return {"ok": True, "exp": ses["exp"]}

@app.post("/protected/logout")
def protected_logout(Authorization: Optional[str] = Header(default=None)):
    if not Authorization or not Authorization.lower().startswith("bearer "):
        return {"ok": True}
    sid = Authorization.split(" ", 1)[1].strip()
    SESSIONS.pop(sid, None)
    return {"ok": True}

# ---------- Shadow WS & ingest ----------
@app.websocket("/shadow/ws")
async def shadow_ws(ws: WebSocket):
    await ws_manager.connect(ws)
    try:
        while True:
            await ws.receive_text()  # no-op; keep alive
    except WebSocketDisconnect:
        await ws_manager.disconnect(ws)

class ShadowFrame(BaseModel):
    ts: int
    kid: Optional[str] = None
    vec: Dict[str, Any] = {}

@app.post("/shadow/ingest")
async def shadow_ingest(frame: ShadowFrame):
    os.makedirs("logs", exist_ok=True)
    with open("logs/shadow.jsonl", "a") as f:
        f.write(json.dumps(frame.dict()) + "\n")
    await ws_manager.broadcast(frame.dict())
    return {"ok": True}

# ---------- Mobile UI (/mobile) ----------
MOBILE_HTML = """<!doctype html>
<meta charset="utf-8"><title>Guardian — Mobile Signer</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{font-family:ui-sans-serif,system-ui;margin:16px;line-height:1.4}
h1{font-size:22px;margin:0 0 12px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.card{border:1px solid #eee;border-radius:12px;padding:12px}
textarea,input,button{width:100%;padding:8px;border:1px solid #ddd;border-radius:8px}
pre{background:#f7f7f7;border:1px solid #eee;border-radius:8px;padding:12px;white-space:pre-wrap}
.btn{background:#f8f8ff;border:1px solid #ccd;cursor:pointer}
.btn:active{transform:scale(.99)}
.row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.mono{font-family:ui-monospace,Menlo,Consolas,monospace}
</style>
<h1>Guardian — Mobile Signer</h1>

<div class="grid">
  <div class="card">
    <b>1) Klucz prywatny (PRIV, seed 32B)</b>
    <input id="kid" placeholder="dev-key-1" class="mono" style="margin:8px 0">
    <textarea id="priv" rows="3" placeholder="Wklej PRIV z terminala" class="mono"></textarea>
    <div class="mono" style="color:#666;margin:6px 0">PRIV to seed 32B w base64url (z terminala). Strona zapisuje go lokalnie w przeglądarce.</div>
    <button id="save" class="btn">💾 Zapisz w przeglądarce</button>
  </div>

  <div class="card">
    <b>2) Rejestracja PUB</b>
    <button id="register" class="btn">🧾 Zarejestruj PUB na serwerze</button>
    <pre id="regOut"></pre>
  </div>

  <div class="card">
    <b>3) Challenge</b>
    <button id="getCh" class="btn">🎯 Pobierz /auth/challenge</button>
    <pre id="chOut"></pre>
  </div>

  <div class="card">
    <b>4) Podpisz JWS i zweryfikuj</b>
    <button id="verify" class="btn">🔐 Podpisz & /guardian/verify</button>
    <pre id="verOut"></pre>
  </div>

  <div class="card">
    <b>5) Token (Bearer)</b>
    <div class="row">
      <button id="ping" class="btn">🔒 Ping /protected/hello</button>
      <button id="refresh" class="btn">🔄 Refresh</button>
    </div>
    <div class="row" style="margin-top:8px">
      <button id="logout" class="btn">🚪 Logout</button>
      <div id="ttl" class="mono" style="border:1px dashed #ddd;border-radius:8px;display:flex;align-items:center;justify-content:center">Wygasa za: -s</div>
    </div>
    <pre id="tokOut"></pre>
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/tweetnacl@1.0.3/nacl.min.js"></script>
<script>
const enc = new TextEncoder(), dec = new TextDecoder();
const b64u = b => btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\\+/g,'-').replace(/\\//g,'_').replace(/=+$/,'');
const fromB64U = s => { s=s.replace(/-/g,'+').replace(/_/g,'/'); while(s.length%4) s+='='; const bin=atob(s), out=new Uint8Array(bin.length); for(let i=0;i<bin.length;i++) out[i]=bin.charCodeAt(i); return out; };

const LS_PRIV='guardian_priv_seed', LS_KID='guardian_kid', LS_SID='guardian_sid', LS_EXP='guardian_exp';
const $ = id => document.getElementById(id);

// restore priv/kid
$('kid').value = localStorage.getItem(LS_KID)||'dev-key-1';
$('priv').value = localStorage.getItem(LS_PRIV)||'';
$('save').onclick = ()=>{ localStorage.setItem(LS_KID, $('kid').value.trim()); localStorage.setItem(LS_PRIV, $('priv').value.trim()); alert('Zapisano PRIV/KID.'); };

function getKeypair(){
  const seedB64u = $('priv').value.trim();
  if(!seedB64u) throw new Error('Brak PRIV');
  const seed = fromB64U(seedB64u);
  if(seed.length!==32) throw new Error('PRIV musi być dokładnie 32B (base64url)');
  return nacl.sign.keyPair.fromSeed(seed);
}

$('register').onclick = async ()=>{
  try{
    const kp = getKeypair();
    const kid = $('kid').value.trim() || 'dev-key-1';
    const pubB64u = b64u(kp.publicKey);
    const r = await fetch('/admin/register_pubkey',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kid:kid,pub:pubB64u})});
    $('regOut').textContent = await r.text();
  }catch(e){ $('regOut').textContent = 'ERR: '+e.message; }
};

let lastCh = null, sid = localStorage.getItem(LS_SID)||'', exp = parseInt(localStorage.getItem(LS_EXP)||'0');
const ttlBox = $('ttl'); const tokOut = $('tokOut');
function renderTTL(){
  if(!exp){ ttlBox.textContent='Wygasa za: -s'; return; }
  const s = Math.max(0, exp - Math.floor(Date.now()/1000));
  ttlBox.textContent = 'Wygasa za: '+s+'s';
}
setInterval(renderTTL, 1000); renderTTL();

$('getCh').onclick = async ()=>{
  const r = await fetch('/auth/challenge'); lastCh = await r.json();
  $('chOut').textContent = JSON.stringify(lastCh,null,2);
};

$('verify').onclick = async ()=>{
  try{
    if(!lastCh) throw new Error('Najpierw pobierz challenge');
    const kid = $('kid').value.trim() || 'dev-key-1';
    const hdr = {alg:"EdDSA",typ:"JWT",kid};
    const pld = {aud:lastCh.aud, nonce:lastCh.nonce, ts: Math.floor(Date.now()/1000)};
    const h_b = b64u(enc.encode(JSON.stringify(hdr)));
    const p_b = b64u(enc.encode(JSON.stringify(pld)));
    const msg = enc.encode(h_b+"."+p_b);
    const kp = getKeypair();
    const sig = nacl.sign.detached(msg, kp.secretKey);
    const jws = h_b+"."+p_b+"."+b64u(sig);
    const r = await fetch('/guardian/verify',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({jws})});
    const x = await r.json(); $('verOut').textContent = JSON.stringify(x,null,2);
    if(x.ok && x.session){ sid = x.session; exp = x.exp; localStorage.setItem(LS_SID,sid); localStorage.setItem(LS_EXP,String(exp)); renderTTL(); }
  }catch(e){ $('verOut').textContent = 'ERR: '+e.message; }
};

async function authFetch(url, opt={}){
  opt.headers = Object.assign({}, opt.headers||{}, {'Authorization':'Bearer '+(sid||'')});
  return fetch(url,opt);
}

$('ping').onclick = async ()=>{
  const r = await authFetch('/protected/hello'); tokOut.textContent = await r.text();
};
$('refresh').onclick = async ()=>{
  const r = await authFetch('/protected/refresh',{method:'POST'}); const x = await r.json();
  if(x.ok && x.exp){ exp = x.exp; localStorage.setItem(LS_EXP,String(exp)); } tokOut.textContent = JSON.stringify(x,null,2);
};
$('logout').onclick = async ()=>{
  await authFetch('/protected/logout',{method:'POST'}); sid=''; exp=0; localStorage.removeItem(LS_SID); localStorage.removeItem(LS_EXP); renderTTL(); tokOut.textContent='{"ok":true}';
};
</script>
"""
@app.get("/mobile", response_class=HTMLResponse)
def mobile_page():
    return HTMLResponse(MOBILE_HTML)

# ---------- Health ----------
@app.get("/healthz")
def healthz():
    return PlainTextResponse("ok")


