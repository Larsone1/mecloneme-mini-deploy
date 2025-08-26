# backend/app.py
import asyncio
import base64
import hashlib
import json
import os
import time
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError

# ========= helpers =========

def b64u_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")

def b64u_decode(s: str) -> bytes:
    s += "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s.encode())

# ========= config/env =========

NONCE_TTL    = int(os.getenv("NONCE_TTL", "300"))     # 5 min
SESSION_TTL  = int(os.getenv("SESSION_TTL", "900"))   # 15 min
RATE_MAX     = int(os.getenv("RATE_MAX", "30"))       # req per window
RATE_WINDOW  = int(os.getenv("RATE_WINDOW", "10"))    # seconds

# ========= stores (in-memory; demo) =========

NONCES: Dict[str, int]   = {}            # nonce -> expiry ts
PUBKEYS: Dict[str, str]  = {}            # kid   -> pubkey(base64url 32B)
SESSIONS: Dict[str, Dict[str, Any]] = {} # sess  -> {"kid":..., "exp":...}
RATE: Dict[str, List[int]] = {}          # ip -> [timestamps]

# ========= rate limit =========

def rate_check(request: Request) -> None:
    ip = (request.client.host if request and request.client else "unknown")
    now = int(time.time())
    window = RATE.setdefault(ip, [])
    # drop old
    while window and window[0] <= now - RATE_WINDOW:
        window.pop(0)
    window.append(now)
    if len(window) > RATE_MAX:
        raise HTTPException(429, "rate-limit")

# ========= models =========

class ChallengeResp(BaseModel):
    nonce: str
    aud: str
    ts: int

class VerifyReq(BaseModel):
    jws: str

class PubReq(BaseModel):
    kid: str
    pub: str  # base64url(32B ed25519)

class ShadowFrame(BaseModel):
    ts: int
    kid: Optional[str] = None
    vec: Dict[str, Any] = {}

# ========= WS manager =========

class WSManager:
    def __init__(self) -> None:
        self.active: List[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.append(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, data: Dict[str, Any]) -> None:
        text = json.dumps(data)
        stale: List[WebSocket] = []
        for ws in list(self.active):
            try:
                await ws.send_text(text)
            except Exception:
                stale.append(ws)
        for ws in stale:
            try:
                await self.disconnect(ws)
            except Exception:
                pass

ws_manager = WSManager()

# ========= FastAPI =========

app = FastAPI(title="MeCloneMe API (mini)")

# ========= Mini panel (desktop) =========

PANEL_HTML = """<!doctype html>
<meta charset="utf-8"/>
<title>Guardian — mini panel</title>
<style>
  body{font-family:ui-sans-serif,system-ui,sans-serif;padding:16px}
  h1{margin:0 0 12px}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
  .card{border:1px solid #eee;border-radius:12px;padding:12px}
  pre{background:#f7f7f7;border:1px solid #eee;border-radius:8px;
      padding:12px;height:260px;overflow:auto;white-space:pre-wrap}
  .row{border:1px solid #eee;border-radius:12px;padding:12px;margin-top:16px}
  .muted{color:#666}
  .status{margin-left:8px}
  .barwrap{background:#eee;height:8px;border-radius:8px}
  .bar{height:100%;width:0%;background:#3ba55d;border-radius:8px}
  .ctl{margin-top:10px}
  button{padding:6px 10px;border:1px solid #ddd;border-radius:8px;background:#fafafa;cursor:pointer}
</style>
<h1>Guardian — mini panel</h1>

<div class="grid">
  <div class="card">
    <h2>Challenge</h2>
    <pre id="challenge">…</pre>
  </div>
  <div class="card">
    <h2>Live log <span id="ws-status" class="status muted">WS: connecting…</span></h2>
    <pre id="log"></pre>
  </div>
</div>

<div class="row">
  <h3>Postęp projektu (tylko lokalnie — zapis w przeglądarce)</h3>
  <div id="progress-root"></div>
</div>

<script>
(async function () {
  const challengeBox = document.getElementById('challenge');
  const log = document.getElementById('log');
  const statusEl = document.getElementById('ws-status');

  function setStatus(txt, color){ statusEl.textContent = 'WS: '+txt; statusEl.style.color = color||'#0a0'; }

  // 1) Show a fresh challenge
  try{
    const r = await fetch('/auth/challenge');
    const x = await r.json();
    challengeBox.textContent = JSON.stringify(x,null,2);
  }catch(e){
    challengeBox.textContent = 'API offline';
  }

  // 2) WebSocket (with auto-reconnect)
  let ws, retry;
  function connectWS(){
    clearTimeout(retry);
    try{
      const proto = location.protocol==='https:' ? 'wss' : 'ws';
      ws = new WebSocket(`${proto}://${location.host}/shadow/ws`);
      setStatus('connecting…','#999');

      ws.onopen = () => setStatus('connected','#0a0');
      ws.onerror = () => setStatus('error','#c00');
      ws.onclose = () => { setStatus('reconnecting…','#c90'); retry=setTimeout(connectWS,2000); }
      ws.onmessage = (e)=>{
        try{
          const m = JSON.parse(e.data);
          log.textContent += JSON.stringify(m)+'\\n';
          log.scrollTop = log.scrollHeight;
        }catch(_){}
      };
    }catch(_){
      setStatus('error','#c00');
      retry=setTimeout(connectWS,2000);
    }
  }
  connectWS();

  // 3) Progress bars (saved in localStorage)
  const FIELDS = [
    ['Guardian/Auth','prog-guard'],
    ['AR Engine (R&D)','prog-ar'],
    ['App Shell / UI','prog-ui'],
    ['Cloud & Deploy','prog-cloud'],
    ['MVP (całość)','prog-mvp']
  ];
  const root = document.getElementById('progress-root');
  FIELDS.forEach(([label,key])=>{
    const row=document.createElement('div');
    row.style.display='grid';
    row.style.gridTemplateColumns='160px 1fr 48px 40px';
    row.style.alignItems='center'; row.style.gap='8px'; row.style.margin='6px 0';

    const name=document.createElement('div'); name.textContent=label;

    const barwrap=document.createElement('div'); barwrap.className='barwrap';
    const bar=document.createElement('div'); bar.className='bar'; barwrap.appendChild(bar);

    const input=document.createElement('input'); input.type='number'; input.min='0'; input.max='100';
    input.value=localStorage.getItem(key)||'0';
    const pct=document.createElement('div'); pct.textContent=(input.value|0)+'%';

    function update(){
      const v=Math.max(0,Math.min(100,parseInt(input.value||'0',10)));
      input.value=String(v); pct.textContent=v+'%'; bar.style.width=v+'%';
      localStorage.setItem(key,String(v));
    }
    input.oninput=update; update();

    row.appendChild(name); row.appendChild(barwrap); row.appendChild(input); row.appendChild(pct);
    root.appendChild(row);
  });

  const ctl=document.createElement('div'); ctl.className='ctl';
  const save=document.createElement('button'); save.textContent='💾 Zapisz';
  const reset=document.createElement('button'); reset.textContent='↺ Reset'; reset.style.marginLeft='8px';
  save.onclick=()=>alert('Zapisane lokalnie ✅');
  reset.onclick=()=>{ localStorage.clear(); location.reload(); };
  ctl.appendChild(save); ctl.appendChild(reset); root.appendChild(ctl);
})();
</script>
"""

@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(PANEL_HTML)

# ========= WebSocket & shadow ingest =========

@app.websocket("/shadow/ws")
async def shadow_ws(ws: WebSocket):
    await ws_manager.connect(ws)
    try:
        # echo loop (we don't consume messages now)
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        await ws_manager.disconnect(ws)

@app.post("/shadow/ingest")
async def shadow_ingest(frame: ShadowFrame):
    # (optional) persist a simple jsonl
    try:
        os.makedirs("logs", exist_ok=True)
        with open("logs/shadow.jsonl", "a") as f:
            f.write(json.dumps(frame.dict()) + "\n")
    except Exception:
        pass
    await ws_manager.broadcast(frame.dict())
    return {"ok": True}

# ========= Challenge / Verify =========

@app.get("/auth/challenge", response_model=ChallengeResp)
def challenge(request: Request, aud: str = "mobile"):
    rate_check(request)
    now = int(time.time())
    # generate 16B nonce hex
    nonce = os.urandom(16).hex()
    NONCES[nonce] = now + NONCE_TTL

    # cleanup old
    for n, exp in list(NONCES.items()):
        if exp < now:
            NONCES.pop(n, None)

    return {"nonce": nonce, "aud": aud, "ts": now}

@app.post("/guardian/verify")
async def guardian_verify(req: VerifyReq, request: Request):
    rate_check(request)
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
        if not kid or kid not in PUBKEYS:
            return {"ok": False, "reason": "unknown-kid"}

        verify_key = VerifyKey(b64u_decode(PUBKEYS[kid]))
        signed = (h_b + "." + p_b).encode()

        try:
            verify_key.verify(signed, sig)
        except BadSignatureError:
            return {"ok": False, "reason": "bad-signature"}

        now = int(time.time())
        try:
            ts = int(payload["ts"])
        except Exception:
            return {"ok": False, "reason": "bad-ts"}

        if abs(now - ts) > NONCE_TTL:
            return {"ok": False, "reason": "nonce-expired"}

        aud = payload.get("aud")
        nonce = payload.get("nonce")
        if not aud or not nonce:
            return {"ok": False, "reason": "missing-claims"}

        exp = NONCES.get(nonce)
        if not exp or exp < now:
            return {"ok": False, "reason": "nonce-expired"}

        # consume nonce
        NONCES.pop(nonce, None)

        # mint short session
        sess = "sess_" + hashlib.sha256(
            f"{kid}.{now}.{os.urandom(8)}".encode()
        ).hexdigest()[:24]
        SESSIONS[sess] = {"kid": kid, "exp": now + SESSION_TTL}

        # live log
        frame = {"ts": now, "kid": kid, "vec": {"auth": "ok", "aud": aud}}
        asyncio.create_task(ws_manager.broadcast(frame))

        return {"ok": True, "payload": payload, "session": sess, "exp": SESSIONS[sess]["exp"]}
    except Exception as e:
        return {"ok": False, "reason": "server-error", "detail": str(e)}

# ========= session utils / protected =========

def require_bearer(request: Request) -> Dict[str, Any]:
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth or not auth.lower().startswith("bearer "):
        raise HTTPException(401, "missing-token")
    token = auth.split(" ", 1)[1].strip()
    sess = SESSIONS.get(token)
    if not sess:
        raise HTTPException(401, "invalid-token")
    now = int(time.time())
    if sess["exp"] < now:
        SESSIONS.pop(token, None)
        raise HTTPException(401, "expired")
    return {"id": token, **sess}

@app.get("/protected/hello")
def protected_hello(request: Request):
    sess = require_bearer(request)
    return {"ok": True, "msg": "hello dev-user", "kid": sess["kid"], "exp": sess["exp"]}

@app.post("/guardian/refresh")
def guardian_refresh(request: Request):
    sess = require_bearer(request)
    now = int(time.time())
    sess["exp"] = now + SESSION_TTL
    SESSIONS[sess["id"]] = {"kid": sess["kid"], "exp": sess["exp"]}
    return {"ok": True, "exp": sess["exp"]}

@app.post("/guardian/logout")
def guardian_logout(request: Request):
    sess = require_bearer(request)
    SESSIONS.pop(sess["id"], None)
    return {"ok": True}

# ========= admin: register key =========

@app.post("/admin/register_pubkey")
def register_pubkey(req: PubReq, request: Request):
    rate_check(request)
    try:
        if len(b64u_decode(req.pub)) != 32:
            return {"ok": False, "reason": "bad-pubkey"}
    except Exception:
        return {"ok": False, "reason": "bad-pubkey"}
    PUBKEYS[req.kid] = req.pub
    return {"ok": True, "registered": list(PUBKEYS.keys())}

# ========= Mobile signer (demo UI) =========

MOBILE_HTML = """<!doctype html>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Guardian — Mobile Signer</title>
<style>
  body{font-family:ui-sans-serif,system-ui,sans-serif;line-height:1.45;margin:16px}
  h1{font-size:22px;margin:0 0 14px}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
  .col{display:grid;gap:14px}
  .card{border:1px solid #eee;border-radius:12px;padding:10px}
  b{display:block;margin:0 0 6px}
  input,textarea,button{width:100%;padding:8px;border:1px solid #ddd;border-radius:8px}
  textarea{height:70px}
  pre{background:#f7f7f7;border:1px solid #eee;border-radius:8px;padding:10px;white-space:pre-wrap}
  .row{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:6px}
  .muted{color:#666;font-size:12px}
  .btn{background:#fafafa;cursor:pointer}
  .ok{color:#0a0}
</style>

<h1>Guardian — Mobile Signer</h1>

<div class="grid">
  <div class="col">
    <div class="card">
      <b>1) Klucz prywatny (PRIV, seed 32B)</b>
      <input id="kid" placeholder="dev-key-1" value="dev-key-1"/>
      <textarea id="priv" placeholder="Wklej PRIV z terminala"></textarea>
      <div class="muted">PRIV to seed 32B w base64url (z terminala). Strona zapisuje go lokalnie w przeglądarce.</div>
      <button id="save" class="btn">💾 Zapisz w przeglądarce</button>
    </div>

    <div class="card">
      <b>3) Challenge</b>
      <button id="getCh" class="btn">🎯 Pobierz /auth/challenge</button>
      <pre id="chOut"></pre>
    </div>

    <div class="card">
      <b>5) Token (Bearer)</b>
      <div class="row">
        <button id="ping" class="btn">🔒 Ping /protected/hello</button>
        <button id="refresh" class="btn">🔄 Refresh</button>
      </div>
      <div class="row">
        <button id="logout" class="btn">🚪 Logout</button>
        <div class="muted" id="expLbl">Wygasa za: –s</div>
      </div>
      <pre id="tokOut">{ "ok": true }</pre>
    </div>
  </div>

  <div class="col">
    <div class="card">
      <b>2) Rejestracja PUB</b>
      <button id="register" class="btn">🪪 Zarejestruj PUB na serwerze</button>
      <pre id="regOut"></pre>
    </div>

    <div class="card">
      <b>4) Podpisz JWS i zweryfikuj</b>
      <button id="verify" class="btn">🔐 Podpisz &amp; /guardian/verify</button>
      <pre id="verOut"></pre>
    </div>
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/tweetnacl@1.0.3/nacl.min.js"></script>
<script>
const enc = new TextEncoder(), dec = new TextDecoder();
const b64u = b => btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\\+/g,'-').replace(/\\//g,'_').replace(/=+$/,'');
const fromB64U = s => { s=s.replace(/-/g,'+').replace(/_/g,'/'); while(s.length%4)s+='='; const bin=atob(s), out=new Uint8Array(bin.length); for(let i=0;i<bin.length;i++) out[i]=bin.charCodeAt(i); return out; };

const LS_KEY = "guardian_priv_seed";
const $ = id => document.getElementById(id);
$("priv").value = localStorage.getItem(LS_KEY)||"";
$("save").onclick = ()=>{ localStorage.setItem(LS_KEY, $("priv").value.trim()); alert("Zapisano PRIV w przeglądarce."); };

function getKeypair(){
  const seedB64U = $("priv").value.trim();
  if(!seedB64U) throw new Error("Brak PRIV");
  const seedBytes = fromB64U(seedB64U);
  if(seedBytes.length !== 32) throw new Error("PRIV musi być 32B (base64url)");
  return nacl.sign.keyPair.fromSeed(seedBytes);
}

let lastChallenge = null;
$("getCh").onclick = async () => {
  const r = await fetch('/auth/challenge');
  lastChallenge = await r.json();
  $("chOut").textContent = JSON.stringify(lastChallenge,null,2);
};

$("register").onclick = async () => {
  try{
    const kp = getKeypair();
    const kid = $("kid").value.trim() || "dev-key-1";
    const pubB64U = b64u(kp.publicKey);
    const r = await fetch('/admin/register_pubkey', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({kid, pub: pubB64U})
    });
    $("regOut").textContent = await r.text();
  }catch(e){ $("regOut").textContent = "ERR: "+e.message; }
};

let sess = null; // "sess_..."
let sessExp = 0;

function renderExp(){
  if(!sessExp){ $("expLbl").textContent = "Wygasa za: –s"; return; }
  const left = Math.max(0, Math.floor(sessExp - (Date.now()/1000)));
  $("expLbl").textContent = "Wygasa za: " + left + "s";
}
setInterval(renderExp, 1000);

$("verify").onclick = async () => {
  try{
    if(!lastChallenge) throw new Error("Najpierw pobierz challenge.");
    const kid = $("kid").value.trim() || "dev-key-1";
    const hdr = {alg:"EdDSA", typ:"JWT", kid};
    const pld = {aud:lastChallenge.aud, nonce:lastChallenge.nonce, ts: Math.floor(Date.now()/1000)};

    const h_b = b64u(enc.encode(JSON.stringify(hdr)));
    const p_b = b64u(enc.encode(JSON.stringify(pld)));
    const msg = enc.encode(h_b+"."+p_b);

    const kp = getKeypair();
    const sig = nacl.sign.detached(msg, kp.secretKey);
    const jws = h_b+"."+p_b+"."+b64u(sig);

    const r = await fetch('/guardian/verify', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({jws})
    });
    const out = await r.json();
    $("verOut").textContent = JSON.stringify(out,null,2);

    if(out.ok && out.session){
      sess = out.session; sessExp = out.exp||0;
      localStorage.setItem("sess_token", sess);
      renderExp();
    }
  }catch(e){ $("verOut").textContent = "ERR: "+e.message; }
};

async function authed(path, method='GET'){
  const token = sess || localStorage.getItem("sess_token") || "";
  return fetch(path, { method, headers: { "Authorization": "Bearer "+token } });
}

$("ping").onclick = async () => {
  const r = await authed('/protected/hello');
  const x = await r.json();
  $("tokOut").textContent = JSON.stringify(x,null,2);
};

$("refresh").onclick = async () => {
  const r = await authed('/guardian/refresh','POST');
  const x = await r.json();
  $("tokOut").textContent = JSON.stringify(x,null,2);
  if(x.ok && x.exp){ sessExp = x.exp; renderExp(); }
};

$("logout").onclick = async () => {
  const r = await authed('/guardian/logout','POST');
  const x = await r.json();
  $("tokOut").textContent = JSON.stringify(x,null,2);
  sess = null; sessExp = 0; localStorage.removeItem("sess_token"); renderExp();
};
</script>
"""

@app.get("/mobile", response_class=HTMLResponse)
def mobile_page():
    return HTMLResponse(MOBILE_HTML)

