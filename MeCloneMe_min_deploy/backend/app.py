import os, time, json, base64, secrets, asyncio, io, statistics
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
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
API_VERSION = os.getenv("API_VERSION","0.1.2")
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
LOG_DIR  = "logs"
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LOG_DIR,  exist_ok=True)
PUBKEYS_PATH   = os.path.join(DATA_DIR, "pubkeys.json")
SESSIONS_PATH  = os.path.join(DATA_DIR, "sessions.json")
EVENTS_JSONL   = os.path.join(LOG_DIR,  "events.jsonl")  # audit/admin
SHADOW_JSONL   = os.path.join(LOG_DIR,  "shadow.jsonl")  # live frames

BOOT_TS = int(time.time())
REQ_COUNT = 0

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

# ===== Events (JSONL) =====
def write_event(kind: str, **data) -> None:
    rec = {"ts": int(time.time()), "kind": kind, **data}
    try:
        with open(EVENTS_JSONL, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass

def tail_jsonl(path: str, n: int) -> List[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()[-n:]
        out = []
        for ln in lines:
            try: out.append(json.loads(ln))
            except Exception: pass
        return out
    except FileNotFoundError:
        return []

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

# ===== HTTP middleware: audit + metrics =====
@app.middleware("http")
async def audit_mw(request: Request, call_next):
    global REQ_COUNT
    t0 = time.perf_counter()
    ip = request.client.host if request.client else "?"
    ua = (request.headers.get("user-agent") or "")[:160]
    path = request.url.path
    method = request.method
    try:
        response = await call_next(request)
        status = getattr(response, "status_code", 200)
        return response
    finally:
        ms = int((time.perf_counter() - t0) * 1000)
        REQ_COUNT += 1
        write_event("http", ip=ip, ua=ua, method=method, path=path, status=status, ms=ms)

# ===== Mini panel (admin + progress + quick tests) =====
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

<div style="border:1px solid #eee;border-radius:8px;padding:12px;margin-top:16px">
  <h2>Admin — szybkie testy</h2>
  <div style="margin:6px 0">Token (Bearer) z <code>/mobile</code> po <i>verify</i>:
    <input id="admTok" placeholder="sess_xxx" style="width:320px">
  </div>
  <div style="display:flex;gap:8px;flex-wrap:wrap;margin:6px 0">
    <button id="btnTail">📜 Tail (50)</button>
    <button id="btnCSV">⬇️ CSV (200)</button>
    <button id="btnSummary">📊 Summary (500)</button>
    <button id="btnHealth">🩺 Health detail</button>
    <button id="btnPurge" style="color:#b71c1c;border-color:#b71c1c">🧹 Purge log</button>
  </div>
  <pre id="admOut" style="background:#f7f7f7;border:1px solid #eee;border-radius:8px;min-height:120px;padding:12px"></pre>
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

(async function init(){
  try{
    const r = await fetch('/auth/challenge');
    const j = await r.json();
    $("challenge").textContent = JSON.stringify(j,null,2);
  }catch(e){ $("challenge").textContent = "API offline"; }

  const wsUrl = (location.protocol==="https:"?"wss":"ws")+"://"+location.host+"/shadow/ws";
  try{
    const ws = new WebSocket(wsUrl);
    ws.onmessage = (ev)=>{
      try{ log(JSON.stringify(JSON.parse(ev.data))); }
      catch(e){ log(ev.data); }
    };
  }catch(e){ const el=document.querySelector("#ws"); el.textContent="WS: error"; el.style.color="#b71c1c"; }

  const state = JSON.parse(localStorage.getItem(LS_KEY)||"{}");
  renderBars(state);
  document.getElementById("save").onclick = ()=>localStorage.setItem(LS_KEY, JSON.stringify(state));
  document.getElementById("reset").onclick = ()=>{ localStorage.removeItem(LS_KEY); location.reload(); };

  // Admin quick tests
  function tok(){ return $("admTok").value.trim(); }
  $("btnTail").onclick = async ()=>{
    const r = await fetch('/admin/events/tail?n=50',{headers:{Authorization:'Bearer '+tok()}});
    $("admOut").textContent = await r.text();
  };
  $("btnCSV").onclick = async ()=>{
    const r = await fetch('/admin/events/export.csv?n=200',{headers:{Authorization:'Bearer '+tok()}});
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'events.csv'; a.click();
    URL.revokeObjectURL(url);
    $("admOut").textContent = "Pobrano events.csv";
  };
  $("btnSummary").onclick = async ()=>{
    const r = await fetch('/api/logs/summary?n=500');
    $("admOut").textContent = JSON.stringify(await r.json(),null,2);
  };
  $("btnHealth").onclick = async ()=>{
    const r = await fetch('/api/health/detail?n=200');
    $("admOut").textContent = JSON.stringify(await r.json(),null,2);
  };
  $("btnPurge").onclick = async ()=>{
    if(!confirm('Na pewno usunąć zawartość events.jsonl?')) return;
    const r = await fetch('/admin/events/purge',{method:'POST', headers:{Authorization:'Bearer '+tok()}});
    $("admOut").textContent = await r.text();
  };
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
  return nacl.sign.keyPair.fromSeed(seed);
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
function tick(){ const eta = Math.max(0, exp - Math.floor(Date.now()/1000)); $("eta").textContent = eta+"s"; }

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

def _auth_token(request: Request) -> str:
    auth = request.headers.get("authorization") or ""
    return auth.split(" ",1)[1] if auth.lower().startswith("bearer ") else ""

# ===== Routes: UI roots =====
@app.get("/", response_class=HTMLResponse)
def root():
    return HTMLResponse(PANEL_HTML)

@app.get("/mobile", response_class=HTMLResponse)
def mobile_page():
    return HTMLResponse(MOBILE_HTML)

# ===== Health / Version / Metrics =====
@app.get("/healthz")
def healthz():
    return {"ok": True, "ts": int(time.time())}

@app.get("/api/health")
def api_health():
    return {
        "ok": True,
        "version": API_VERSION,
        "ts": int(time.time()),
        "uptime": int(time.time()) - BOOT_TS,
        "counts": {
            "requests": REQ_COUNT,
            "nonces": len(NONCES),
            "pubkeys": len(PUBKEYS),
            "sessions": len(SESSIONS)
        }
    }

@app.get("/api/version")
def api_version():
    git = os.getenv("RENDER_GIT_COMMIT") or os.getenv("GIT_SHA") or ""
    svc = os.getenv("RENDER_SERVICE_NAME") or ""
    return {"ok": True, "version": API_VERSION, "boot_ts": BOOT_TS, "git": git[:12], "service": svc}

@app.get("/api/metrics")
def api_metrics():
    return {"ok": True, "rate": {"window_s": RATE_WINDOW, "max": RATE_MAX}, "counts": {"ip_slots": len(RATE)}}

# ===== WS + Shadow ingest =====
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
    try:
        with open(SHADOW_JSONL,"a",encoding="utf-8") as f:
            f.write(json.dumps(frame.dict())+"\n")
    except Exception:
        pass
    await ws_manager.broadcast(frame.dict())
    write_event("shadow", **frame.dict())
    return ok()

# ===== Auth flow =====
@app.get("/auth/challenge")
async def challenge(request: Request, aud: str = "mobile"):
    ip = request.client.host if request.client else "?"
    if not rate_check(ip):
        await emit("rate", ip=ip, status="limit")
        write_event("rate_limit", ip=ip, path="/auth/challenge")
        return bad("rate-limit")
    now = int(time.time())
    nonce = secrets.token_hex(16)
    NONCES[nonce] = now + NONCE_TTL
    for n, exp in list(NONCES.items()):
        if exp < now:
            NONCES.pop(n, None)
    await emit("challenge", aud=aud, nonce=nonce)
    write_event("auth.challenge", ip=ip, aud=aud, nonce=nonce)
    return ok(aud=aud, nonce=nonce, ttl=NONCE_TTL)

@app.post("/guardian/register_pubkey")
async def register_pubkey(req: PubKeyReq, request: Request):
    ip = request.client.host if request.client else "?"
    if not rate_check(ip):
        await emit("rate", ip=ip, status="limit")
        write_event("rate_limit", ip=ip, path="/guardian/register_pubkey")
        return bad("rate-limit")
    try:
        if len(b64u_decode(req.pub)) != 32:
            return bad("bad-pubkey")
    except Exception:
        return bad("bad-pubkey")
    PUBKEYS[req.kid] = req.pub
    save_pubkeys()
    await emit("admin", action="register_pubkey", kid=req.kid)
    write_event("auth.register_pubkey", kid=req.kid)
    return ok(registered=list(PUBKEYS.keys()))

@app.post("/guardian/verify")
async def guardian_verify(request: Request, req: VerifyReq):
    ip = request.client.host if request.client else "?"
    if not rate_check(ip):
        await emit("rate", ip=ip, status="limit")
        write_event("rate_limit", ip=ip, path="/guardian/verify")
        return bad("rate-limit")

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

    kid = header.get("kid")
    alg = header.get("alg")
    if alg != "EdDSA" or not kid or kid not in PUBKEYS:
        return bad("bad-header")

    try:
        vk = VerifyKey(b64u_decode(PUBKEYS[kid]))
        vk.verify((h_b+"."+p_b).encode(), sig)
    except BadSignatureError:
        return bad("bad-signature")

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
        NONCES.pop(nonce, None)
    except Exception:
        return bad("bad-payload")

    sid = "sess_" + secrets.token_hex(16)
    sess_exp = now + SESSION_TTL
    SESSIONS[sid] = {"kid": kid, "exp": sess_exp}
    save_sessions()

    await emit("auth", status="ok", aud=aud, kid=kid)
    write_event("auth.verify_ok", kid=kid, aud=aud, sid=sid)
    return ok(payload=payload, session=sid, exp=sess_exp)

@app.get("/protected/hello")
async def protected_hello(request: Request):
    ip = request.client.host if request.client else "?"
    if not rate_check(ip):
        await emit("rate", ip=ip, status="limit")
        write_event("rate_limit", ip=ip, path="/protected/hello")
        return bad("rate-limit")
    token = _auth_token(request)
    sess = require_session(token)
    if not sess:
        await emit("unauth", path="/protected/hello", ip=ip)
        write_event("auth.unauthorized", ip=ip, path="/protected/hello")
        return bad("unauthorized")
    await emit("hello", kid=sess["kid"])
    write_event("hello", kid=sess["kid"])
    return ok(msg="hello dev-user", kid=sess["kid"], exp=sess["exp"])

@app.post("/guardian/refresh")
async def refresh(request: Request):
    token = _auth_token(request)
    sess = require_session(token)
    if not sess:
        await emit("unauth", path="/guardian/refresh")
        return bad("unauthorized")
    sess["exp"] = int(time.time()) + SESSION_TTL
    save_sessions()
    await emit("session", action="refresh", kid=sess["kid"], exp=sess["exp"])
    write_event("auth.refresh", kid=sess["kid"], exp=sess["exp"])
    return ok(exp=sess["exp"])

@app.post("/guardian/logout")
async def logout(request: Request):
    token = _auth_token(request)
    s = SESSIONS.pop(token, None)
    save_sessions()
    if s:
        await emit("session", action="logout", kid=s["kid"])
        write_event("auth.logout", kid=s["kid"])
    return ok()

# ===== Admin: events tail + CSV export + purge =====
@app.get("/admin/events/tail")
def admin_tail(request: Request, n: int = Query(200, ge=1, le=2000)):
    token = _auth_token(request)
    if not require_session(token): return bad("unauthorized")
    return {"ok": True, "items": tail_jsonl(EVENTS_JSONL, n)}

def _flatten(d: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
    out = {}
    for k,v in d.items():
        kk = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict): out.update(_flatten(v, kk))
        else: out[kk] = v
    return out

@app.get("/admin/events/export.csv")
def admin_csv(request: Request, n: int = Query(500, ge=1, le=5000)):
    token = _auth_token(request)
    if not require_session(token): return bad("unauthorized")
    rows = tail_jsonl(EVENTS_JSONL, n)
    headers: List[str] = []
    flat_rows: List[Dict[str, Any]] = []
    for r in rows:
        fr = _flatten(r)
        flat_rows.append(fr)
        for k in fr.keys():
            if k not in headers: headers.append(k)
    buf = io.StringIO()
    buf.write(",".join(headers) + "\n")
    for fr in flat_rows:
        vals = []
        for h in headers:
            v = fr.get(h, "")
            if isinstance(v, (dict, list)): v = json.dumps(v, ensure_ascii=False)
            s = str(v).replace('"','""')
            if any(c in s for c in [",", "\n", '"']): s = f'"{s}"'
            vals.append(s)
        buf.write(",".join(vals) + "\n")
    return PlainTextResponse(buf.getvalue(), media_type="text/csv")

@app.post("/admin/events/purge")
def admin_purge(request: Request):
    token = _auth_token(request)
    if not require_session(token): return bad("unauthorized")
    # backup & truncate
    try:
        if os.path.exists(EVENTS_JSONL):
            ts = int(time.time())
            os.replace(EVENTS_JSONL, f"{EVENTS_JSONL}.{ts}.bak")
        open(EVENTS_JSONL, "w", encoding="utf-8").close()
        write_event("admin.purge")  # zapisze już do nowego, pustego pliku
        return ok(msg="purged")
    except Exception as e:
        return bad("purge-failed", error=str(e))

# ===== Logs summary & health detail =====
def _quantiles(vals: List[int]) -> Dict[str, int]:
    if not vals: return {"p50":0,"p95":0,"avg":0,"max":0}
    p50 = int(statistics.quantiles(vals, n=100)[49]) if len(vals) >= 2 else vals[0]
    p95_idx = max(0, int(len(vals)*0.95) - 1)
    p95 = sorted(vals)[p95_idx]
    avg = int(sum(vals)/len(vals))
    return {"p50":p50, "p95":p95, "avg":avg, "max":max(vals)}

@app.get("/api/logs/summary")
def logs_summary(n: int = Query(1000, ge=10, le=10000)):
    rows = [r for r in tail_jsonl(EVENTS_JSONL, n) if r.get("kind")=="http"]
    total = len(rows)
    by_path: Dict[str,int] = {}
    by_method: Dict[str,int] = {}
    by_status: Dict[str,int] = {}
    lat: List[int] = []
    for r in rows:
        by_path[r.get("path","?")] = by_path.get(r.get("path","?"),0)+1
        by_method[r.get("method","?")] = by_method.get(r.get("method","?"),0)+1
        by_status[str(r.get("status","?"))] = by_status.get(str(r.get("status","?")),0)+1
        if isinstance(r.get("ms"), int): lat.append(r["ms"])
    top_paths = sorted(by_path.items(), key=lambda x: x[1], reverse=True)[:5]
    lat_q = _quantiles(lat)
    err4 = sum(v for k,v in by_status.items() if k.startswith("4"))
    err5 = sum(v for k,v in by_status.items() if k.startswith("5"))
    return {
        "ok": True,
        "total": total,
        "paths_top5": top_paths,
        "methods": by_method,
        "statuses": by_status,
        "latency_ms": lat_q,
        "errors": {"4xx": err4, "5xx": err5}
    }

@app.get("/api/health/detail")
def health_detail(n: int = Query(200, ge=20, le=5000)):
    rows = [r for r in tail_jsonl(EVENTS_JSONL, n) if r.get("kind")=="http"]
    lat = [r["ms"] for r in rows if isinstance(r.get("ms"), int)]
    lat_q = _quantiles(lat)
    codes: Dict[str,int] = {}
    for r in rows:
        k = str(r.get("status","?")); codes[k] = codes.get(k,0)+1
    return {
        "ok": True,
        "ts": int(time.time()),
        "version": API_VERSION,
        "uptime_s": int(time.time()) - BOOT_TS,
        "req_count": REQ_COUNT,
        "rate_window_s": RATE_WINDOW,
        "latency_ms": lat_q,
        "codes": codes,
        "sample_size": len(rows)
    }

# ===== AR engine (stub / R&D) =====
@app.get("/ar/ping")
def ar_ping():
    return {"ok": True, "engine":"stub", "ts": int(time.time())}
