import os, time, json, base64, secrets, asyncio
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError

# ===== Base64url helpers =====
def b64u_decode(s: str) -> bytes:
    s += "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s.encode())

def b64u_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")

# ===== Config (ENV) =====
API_VERSION = os.getenv("API_VERSION","0.1.0")
NONCE_TTL   = int(os.getenv("NONCE_TTL", "300"))   # s
SESSION_TTL = int(os.getenv("SESSION_TTL", "900")) # s
RATE_MAX    = int(os.getenv("RATE_MAX", "30"))     # max calls / window
RATE_WINDOW = int(os.getenv("RATE_WINDOW", "10"))  # s

# ===== In-memory stores (demo) =====
NONCES: Dict[str, int] = {}                 # nonce -> expiry ts
PUBKEYS: Dict[str, str] = {}                # kid -> public key (base64url 32B)
SESSIONS: Dict[str, Dict[str, Any]] = {}    # sid -> {kid, exp}
RATE: Dict[str, List[int]] = {}             # ip -> [timestamps]

# ===== Simple persistence (best-effort) =====
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)
PUBKEYS_PATH  = os.path.join(DATA_DIR, "pubkeys.json")
SESSIONS_PATH = os.path.join(DATA_DIR, "sessions.json")

def _load_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def _save_json(path: str, data) -> None:
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass

def load_stores():
    global PUBKEYS, SESSIONS
    PUBKEYS = _load_json(PUBKEYS_PATH, {})
    SESSIONS = _load_json(SESSIONS_PATH, {})

def save_pubkeys():
    _save_json(PUBKEYS_PATH, PUBKEYS)

def save_sessions():
    _save_json(SESSIONS_PATH, SESSIONS)

# ===== Rate limiting (prosty sliding window) =====
def rate_check(ip: str) -> bool:
    now = int(time.time())
    buf = RATE.get(ip, [])
    buf = [t for t in buf if t > now - RATE_WINDOW]
    if len(buf) >= RATE_MAX:
        RATE[ip] = buf
        return False
    buf.append(now)
    RATE[ip] = buf
    return True

# ===== WebSocket manager =====
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
        msg = json.dumps(data)
        stale: List[WebSocket] = []
        for ws in self.active:
            try:
                await ws.send_text(msg)
            except Exception:
                stale.append(ws)
        for ws in stale:
            await self.disconnect(ws)

ws_manager = WSManager()

# ===== FastAPI =====
app = FastAPI(title="MeCloneMe API (mini)")
app.add_middleware(CORSMiddleware,
    allow_origins=['*'], allow_credentials=True,
    allow_methods=['*'], allow_headers=['*']
)
load_stores()

# ===== Mini panel (admin podgląd + progress lokalnie) =====
PANEL_HTML = """<!doctype html>
<meta charset="utf-8"><title>Guardian — mini panel</title>
<body style="font-family: system-ui, -apple-system, Segoe UI, Roboto; margin:16px">
<h1>Guardian — mini panel</h1>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
  <div style="border:1px solid #eee;border-radius:8px;padding:12px">
    <h2>Challenge</h2>
    <pre id="challenge" style="background:#f7f7f7;border:1px solid #eee;border-radius:8px;min-height:120px;padding:12px"></pre>
  </div>
  <div style="border:1px solid #eee;border-radius:8px;padding:12px">
    <h2>Live log <span id="ws" style="color:#2e7d32">WS: connected</span></h2>
    <pre id="log" style="background:#f7f7f7;border:1px solid #eee;border-radius:8px;height:220px;overflow:auto;padding:12px"></pre>
  </div>
</div>

<div style="border:1px solid #eee;border-radius:8px;padding:12px;margin-top:16px">
  <h2>Postęp projektu (tylko lokalnie — zapis w przeglądarce)</h2>
  <div id="bars"></div>
  <div style="margin-top:8px">
    <button id="save">💾 Zapisz</button>
    <button id="reset">↩︎ Reset</button>
  </div>
</div>

<script>
const $=id=>document.getElementById(id);
const LS_KEY="guardian_progress";
function log(m){ const el=$("log"); el.textContent += m+"\\n"; el.scrollTop = el.scrollHeight; }

const FIELDS = [
  ["Guardian/Auth","ga"],
  ["AR Engine (R&D)","ar"],
  ["App Shell / UI","ui"],
  ["Cloud & Deploy","cd"],
  ["MVP (całość)","mvp"]
];

function renderBars(state){
  const root = $("bars");
  root.innerHTML = "";
  FIELDS.forEach(([label,key])=>{
    const wrap = document.createElement("div");
    wrap.style.display="grid";
    wrap.style.gridTemplateColumns="200px 1fr 42px 42px";
    wrap.style.gap="8px";
    wrap.style.alignItems="center";
    wrap.style.margin="6px 0";
    const lab = document.createElement("div");
    lab.textContent = label;
    const bar = document.createElement("div");
    bar.style.height="8px";bar.style.background="#eee";bar.style.borderRadius="4px";
    const inner = document.createElement("div");
    inner.style.height="100%";inner.style.width=(state[key]||0)+"%";
    inner.style.background="#2e7d32";inner.style.borderRadius="4px";
    bar.appendChild(inner);
    const input = document.createElement("input");
    input.type="number";input.min=0;input.max=100;input.value=state[key]||0;input.style.width="42px";
    const labelPct = document.createElement("div");
    labelPct.textContent=(state[key]||0)+"%";
    input.oninput = ()=>{ 
      let v = Math.max(0, Math.min(100, parseInt(input.value||"0",10)));
      inner.style.width = v+"%";
      labelPct.textContent = v+"%";
      state[key]=v;
    };
    wrap.append(lab, bar, input, labelPct);
    root.appendChild(wrap);
  });
}

function loadState(){
  try{ return JSON.parse(localStorage.getItem(LS_KEY)||"{}"); }catch{ return {}; }
}
function saveState(state){
  localStorage.setItem(LS_KEY, JSON.stringify(state));
}

(async function init(){
  // challenge preview
  try{
    const r = await fetch('/auth/challenge');
    const j = await r.json();
    $("challenge").textContent = JSON.stringify(j,null,2);
  }catch(e){ $("challenge").textContent = "API offline"; }

  // WS live log
  const wsUrl = (location.protocol==="https:"?"wss":"ws")+"://"+location.host+"/shadow/ws";
  try{
    const ws = new WebSocket(wsUrl);
    ws.onmessage = (ev)=>{
      try{
        const j = JSON.parse(ev.data);
        log(JSON.stringify(j));
      }catch(e){ log(ev.data); }
    };
  }catch(e){ $("ws").textContent="WS: error";$("ws").style.color="#b71c1c"; }

  // Progress bars
  const state = loadState(); renderBars(state);
  $("save").onclick = ()=>saveState(state);
  $("reset").onclick = ()=>{ localStorage.removeItem(LS_KEY); location.reload(); };
})();
</script>
</body>
"""

# ===== Mobile demo (Signer) =====
MOBILE_HTML = """<!doctype html>
<meta charset="utf-8"><title>Guardian — Mobile Signer</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  body{font-family:system-ui,-apple-system,Segoe UI,Roboto;margin:16px;line-height:1.25}
  .row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
  .card{border:1px solid #ddd;border-radius:10px;padding:10px}
  pre,textarea,input{width:100%;box-sizing:border-box}
  pre{background:#f7f7f7;border:1px solid #eee;border-radius:8px;white-space:pre-wrap;min-height:80px;padding:10px}
  button{padding:8px 10px;border-radius:8px;border:1px solid #ddd;background:#f9f9f9}
</style>
<h1>Guardian — Mobile Signer</h1>

<div class="row">
  <div class="card">
    <b>1) Klucz prywatny (PRIV, seed 32B)</b>
    <input id="kid" placeholder="dev-key-1" style="margin:8px 0">
    <textarea id="priv" rows="3" placeholder="Wklej PRIV z terminala (base64url)"></textarea>
    <div class="muted">PRIV to seed 32B w base64url. Strona zapisuje go lokalnie w przeglądarce.</div>
    <button id="save" style="margin-top:8px">💾 Zapisz w przeglądarce</button>
  </div>

  <div class="card">
    <b>2) Rejestracja PUB</b>
    <button id="register">🪪 Zarejestruj PUB na serwerze</button>
    <pre id="regOut"></pre>
  </div>
</div>

<div class="row" style="margin-top:10px">
  <div class="card">
    <b>3) Challenge</b>
    <button id="getCh">🎯 Pobierz /auth/challenge</button>
    <pre id="chOut"></pre>
  </div>

  <div class="card">
    <b>4) Podpisz JWS i zweryfikuj</b>
    <button id="verify">🔐 Podpisz & /guardian/verify</button>
    <pre id="verOut"></pre>
  </div>
</div>

<div class="card" style="margin-top:10px">
  <b>5) Token (Bearer)</b><br><br>
  <button id="ping">🔎 /protected/hello</button>
  <button id="refresh">🔁 /guardian/refresh</button>
  <button id="logout">🚪 /guardian/logout</button>
  <pre id="pingOut"></pre>
  <div>ETA tokenu: <span id="eta">-</span></div>
</div>

<script src="https://cdn.jsdelivr.net/npm/tweetnacl@1.0.3/nacl.min.js"></script>
<script>
const $=id=>document.getElementById(id);
const enc = new TextEncoder();

const b64u = b => btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');
const fromB64u = s => {
  s = s.replace(/-/g,'+').replace(/_/g,'/'); while(s.length%4) s+='=';
  const bin = atob(s), out = new Uint8Array(bin.length);
  for(let i=0;i<bin.length;i++) out[i]=bin.charCodeAt(i);
  return out;
};

const LS_KEY="guardian_priv_seed";
$("priv").value = localStorage.getItem(LS_KEY)||"";
$("save").onclick = ()=>{ localStorage.setItem(LS_KEY, $("priv").value.trim()); alert("Zapisano PRIV w przeglądarce."); };

function getKeypair(){
  const seedB64u = $("priv").value.trim();
  if(!seedB64u) throw new Error("Brak PRIV");
  const seed = fromB64u(seedB64u);
  if(seed.length!==32) throw new Error("PRIV (seed) musi być 32 bajty!");
  return nacl.sign.keyPair.fromSeed(seed); // {publicKey(32B), secretKey(64B)}
}

$("register").onclick = async ()=>{
  try{
    const kp = getKeypair();
    const pub = b64u(kp.publicKey);
    const kid = $("kid").value.trim() || "dev-key-1";
    const r = await fetch("/guardian/register_pubkey",{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify({kid, pub})
    });
    $("regOut").textContent = await r.text();
  }catch(e){ $("regOut").textContent = "ERR: "+e.message; }
};

let last = null, session = null, exp=0, ticker=null;
function tick(){
  const eta = Math.max(0, exp - Math.floor(Date.now()/1000));
  $("eta").textContent = eta+"s";
}

$("getCh").onclick = async ()=>{
  const r = await fetch("/auth/challenge");
  last = await r.json();
  $("chOut").textContent = JSON.stringify(last,null,2);
};

$("verify").onclick = async ()=>{
  try{
    if(!last) throw new Error("Najpierw pobierz challenge.");
    const kid = $("kid").value.trim() || "dev-key-1";
    const hdr = {alg:"EdDSA", typ:"JWT", kid};
    const pld = {aud:last.aud, nonce:last.nonce, ts: Math.floor(Date.now()/1000)};
    const h = b64u(enc.encode(JSON.stringify(hdr)));
    const p = b64u(enc.encode(JSON.stringify(pld)));
    const msg = enc.encode(h+"."+p);
    const kp = getKeypair();
    const sig = nacl.sign.detached(msg, kp.secretKey);
    const jws = h+"."+p+"."+b64u(sig);
    const r = await fetch("/guardian/verify",{method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({jws})});
    const j = await r.json();
    $("verOut").textContent = JSON.stringify(j,null,2);
    if(j.ok && j.session){ session=j.session; exp=j.exp; clearInterval(ticker); ticker=setInterval(tick,1000); tick(); }
  }catch(e){ $("verOut").textContent = "ERR: "+e.message; }
};

$("ping").onclick = async ()=>{
  const r = await fetch("/protected/hello",{ headers:{"Authorization":"Bearer "+(session||"")}});
  $("pingOut").textContent = await r.text();
};

$("refresh").onclick = async ()=>{
  const r = await fetch("/guardian/refresh",{ method:"POST", headers:{"Authorization":"Bearer "+(session||"")}});
  const j = await r.json();
  if(j.ok){ exp = j.exp; }
  $("pingOut").textContent = JSON.stringify(j,null,2);
};

$("logout").onclick = async ()=>{
  const r = await fetch("/guardian/logout",{ method:"POST", headers:{"Authorization":"Bearer "+(session||"")}});
  const j = await r.json();
  session=null; exp=0; tick();
  $("pingOut").textContent = JSON.stringify(j,null,2);
};
</script>
"""

# ===== Pydantic models =====
class PubKeyReq(BaseModel):
    kid: str
    pub: str

class VerifyReq(BaseModel):
    jws: str

class ShadowFrame(BaseModel):
    ts: int
    vec: Dict[str, Any]

# ===== Utilities =====
def bad(reason: str, **extra):
    data = {"ok": False, "reason": reason}
    if extra: data.update(extra)
    return JSONResponse(data)

def ok(**payload):
    data = {"ok": True}
    if payload: data.update(payload)
    return JSONResponse(data)

def require_session(token: str) -> Optional[Dict[str, Any]]:
    if not token or not token.startswith("sess_"): 
        return None
    s = SESSIONS.get(token)
    if not s: 
        return None
    if s["exp"] < int(time.time()):
        SESSIONS.pop(token, None)
        save_sessions()
        return None
    return s

async def emit(kind: str, **vec):
    frame = {"ts": int(time.time()), "vec": {kind: vec}}
    await ws_manager.broadcast(frame)

# ===== Routes =====
@app.get("/", response_class=HTMLResponse)
def root():
    return HTMLResponse(PANEL_HTML)

@app.get("/mobile", response_class=HTMLResponse)
def mobile_page():
    return HTMLResponse(MOBILE_HTML)

@app.get("/healthz")
def healthz():
    return {"ok": True, "ts": int(time.time())}

@app.get("/api/health")
def api_health():
    return {
        "ok": True,
        "version": API_VERSION,
        "ts": int(time.time()),
        "counts": {
            "nonces": len(NONCES),
            "pubkeys": len(PUBKEYS),
            "sessions": len(SESSIONS)
        }
    }

@app.websocket("/shadow/ws")
async def ws_shadow(ws: WebSocket):
    await ws_manager.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        await ws_manager.disconnect(ws)

@app.post("/shadow/ingest")
async def shadow_ingest(frame: ShadowFrame):
    # zapis do pliku (best-effort)
    try:
        os.makedirs("logs", exist_ok=True)
        with open("logs/shadow.jsonl","a",encoding="utf-8") as f:
            f.write(json.dumps(frame.dict())+"\n")
    except Exception:
        pass
    await ws_manager.broadcast(frame.dict())
    return ok()

@app.get("/auth/challenge")
async def challenge(request: Request, aud: str = "mobile"):
    ip = request.client.host if request.client else "?"
    if not rate_check(ip):
        await emit("rate", ip=ip, status="limit")
        return bad("rate-limit")
    now = int(time.time())
    nonce = secrets.token_hex(16)
    NONCES[nonce] = now + NONCE_TTL
    # cleanup
    for n, exp in list(NONCES.items()):
        if exp < now:
            NONCES.pop(n, None)
    await emit("challenge", aud=aud, nonce=nonce)
    return ok(aud=aud, nonce=nonce, ttl=NONCE_TTL)

@app.post("/guardian/register_pubkey")
async def register_pubkey(req: PubKeyReq, request: Request):
    ip = request.client.host if request.client else "?"
    if not rate_check(ip):
        await emit("rate", ip=ip, status="limit")
        return bad("rate-limit")
    try:
        if len(b64u_decode(req.pub)) != 32:
            return bad("bad-pubkey")
    except Exception:
        return bad("bad-pubkey")
    PUBKEYS[req.kid] = req.pub
    save_pubkeys()
    await emit("admin", action="register_pubkey", kid=req.kid)
    return ok(registered=list(PUBKEYS.keys()))

@app.post("/guardian/verify")
async def guardian_verify(request: Request, req: VerifyReq):
    ip = request.client.host if request.client else "?"
    if not rate_check(ip):
        await emit("rate", ip=ip, status="limit")
        return bad("rate-limit")

    # compact JWS: h.p.s (base64url)
    try:
        parts = req.jws.split(".")
        if len(parts) != 3:
            return bad("bad-format")
        h_b, p_b, s_b = parts
        header = json.loads(b64u_decode(h_b))
        payload = json.loads(b64u_decode(p_b))
        sig = b64u_decode(s_b)
    except Exception:
        return bad("bad-jws")

    # verify header
    kid = header.get("kid")
    alg = header.get("alg")
    if alg != "EdDSA" or not kid or kid not in PUBKEYS:
        return bad("bad-header")

    # verify signature
    try:
        vk = VerifyKey(b64u_decode(PUBKEYS[kid]))
        vk.verify((h_b+"."+p_b).encode(), sig)
    except BadSignatureError:
        return bad("bad-signature")

    # app checks
    now = int(time.time())
    try:
        if abs(now - int(payload["ts"])) > NONCE_TTL:
            return bad("nonce-expired")
        aud = payload.get("aud")
        nonce = payload.get("nonce")
        if (not aud) or (not nonce):
            return bad("missing-claims")
        exp = NONCES.get(nonce)
        if not exp or exp < now:
            return bad("nonce-expired")
        NONCES.pop(nonce, None)  # consume
    except Exception:
        return bad("bad-payload")

    # create session
    sid = "sess_" + secrets.token_hex(16)
    sess_exp = now + SESSION_TTL
    SESSIONS[sid] = {"kid": kid, "exp": sess_exp}
    save_sessions()

    await emit("auth", status="ok", aud=aud, kid=kid)
    return ok(payload=payload, session=sid, exp=sess_exp)

@app.get("/protected/hello")
async def protected_hello(request: Request):
    ip = request.client.host if request.client else "?"
    if not rate_check(ip):
        await emit("rate", ip=ip, status="limit")
        return bad("rate-limit")
    auth = request.headers.get("authorization") or ""
    token = auth.split(" ",1)[1] if auth.lower().startswith("bearer ") else ""
    sess = require_session(token)
    if not sess:
        await emit("unauth", path="/protected/hello", ip=ip)
        return bad("unauthorized")
    await emit("hello", kid=sess["kid"])
    return ok(msg="hello dev-user", kid=sess["kid"], exp=sess["exp"])

@app.post("/guardian/refresh")
async def refresh(request: Request):
    auth = request.headers.get("authorization") or ""
    token = auth.split(" ",1)[1] if auth.lower().startswith("bearer ") else ""
    sess = require_session(token)
    if not sess:
        await emit("unauth", path="/guardian/refresh")
        return bad("unauthorized")
    sess["exp"] = int(time.time()) + SESSION_TTL
    save_sessions()
    await emit("session", action="refresh", kid=sess["kid"], exp=sess["exp"])
    return ok(exp=sess["exp"])

@app.post("/guardian/logout")
async def logout(request: Request):
    auth = request.headers.get("authorization") or ""
    token = auth.split(" ",1)[1] if auth.lower().startswith("bearer ") else ""
    s = SESSIONS.pop(token, None)
    save_sessions()
    if s:
        await emit("session", action="logout", kid=s["kid"])
    return ok()

# ===== AR engine (stub / R&D) =====
@app.get("/ar/ping")
def ar_ping():
    return {"ok": True, "engine":"stub", "ts": int(time.time())}

