import asyncio, json, time, os, base64, hashlib
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Header
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError

# ---------- helpers: base64url ----------
def b64u_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")

def b64u_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode((s + pad).encode())

# ---------- config / in-memory stores ----------
NONCE_TTL = int(os.getenv("NONCE_TTL", "300"))          # 5 min
SESSION_TTL = int(os.getenv("SESSION_TTL", "1800"))      # 30 min

NONCES: Dict[str, int] = {}       # nonce -> exp
SESSIONS: Dict[str, Dict[str, int]] = {}  # sess -> {"kid":..., "exp": ...}
PUBKEYS: Dict[str, str] = {}      # kid -> 32B public key (base64url)

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

# ---------- FastAPI ----------
app = FastAPI(title="MeCloneMe API")

# ---------- Mini panel (progress + WS) ----------
PANEL_HTML = """<!doctype html><meta charset="utf-8"><title>Guardian — mini panel</title>
<style>
 body{font-family:system-ui, -apple-system, Segoe UI, Roboto, sans-serif;margin:20px}
 h1{margin:0 0 16px}
 .grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
 .card{border:1px solid #eee;border-radius:12px;padding:12px}
 pre{background:#f7f7f7;border:1px solid #eee;padding:12px;border-radius:8px;overflow:auto}
 .ok{color:#27ae60} .err{color:#c0392b}
 .bar{height:10px;background:#eee;border-radius:6px;position:relative}
 .bar>i{position:absolute;left:0;top:0;height:100%;width:0;background:#4CAF50;border-radius:6px}
 .row{display:grid;grid-template-columns:240px 1fr 64px 40px;gap:8px;align-items:center;margin:8px 0}
 small{opacity:.7}
</style>
<h1>Guardian — mini panel</h1>
<div class="grid">
  <div class="card">
    <h2>Challenge</h2>
    <pre id="challenge">...</pre>
  </div>
  <div class="card">
    <h2>Live log <small id="wsState"></small></h2>
    <pre id="log" style="height:260px"></pre>
  </div>
</div>

<div class="card" style="margin-top:16px">
  <h2>Postęp projektu <small>(tylko lokalnie — zapis w przeglądarce)</small></h2>
  <div class="row"><div>Guardian/Auth</div><div class="bar"><i id="b0"></i></div><input id="n0" value="55"><div id="p0">0%</div></div>
  <div class="row"><div>AR Engine (R&D)</div><div class="bar"><i id="b1"></i></div><input id="n1" value="0"><div id="p1">0%</div></div>
  <div class="row"><div>App Shell / UI</div><div class="bar"><i id="b2"></i></div><input id="n2" value="10"><div id="p2">0%</div></div>
  <div class="row"><div>Cloud & Deploy</div><div class="bar"><i id="b3"></i></div><input id="n3" value="40"><div id="p3">0%</div></div>
  <div class="row"><div>MVP (całość)</div><div class="bar"><i id="b4"></i></div><input id="n4" value="8"><div id="p4">0%</div></div>
  <button id="save">💾 Zapisz</button>
  <button id="reset">↺ Reset</button>
</div>

<script>
const $=id=>document.getElementById(id);
fetch('/auth/challenge').then(r=>r.json()).then(x=>{
  $('challenge').textContent=JSON.stringify(x,null,2);
}).catch(()=>{$('challenge').textContent='API offline';});

const log=$('log'), wsState=$('wsState');
try{
  const ws = new WebSocket((location.protocol==='https:'?'wss':'ws')+'://'+location.host+'/shadow/ws');
  ws.onopen=()=>{wsState.textContent='WS: connected';wsState.className='ok';};
  ws.onclose=()=>{wsState.textContent='WS: closed';wsState.className='err';};
  ws.onmessage=e=>{try{const m=JSON.parse(e.data);log.textContent+=JSON.stringify(m)+'\\n';log.scrollTop=log.scrollHeight;}catch{}};
}catch(e){wsState.textContent='WS error';wsState.className='err';}

const K='progress_v1';
function apply(){
  const saved=JSON.parse(localStorage.getItem(K)||'{}');
  for(let i=0;i<5;i++){
    const v=Number((saved['n'+i] ?? $('n'+i).value) || 0);
    $('n'+i).value=v;
    $('b'+i).style.width=(Math.max(0,Math.min(100,v)))+'%';
    $('p'+i).textContent=v+'%';
  }
}
$('save').onclick=()=>{
  const obj={}; for(let i=0;i<5;i++) obj['n'+i]=Number($('n'+i).value||0);
  localStorage.setItem(K,JSON.stringify(obj)); apply();
};
$('reset').onclick=()=>{ localStorage.removeItem(K); apply(); };
apply();
</script>
"""

@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(PANEL_HTML)

# ---------- Shadow WS / ingest ----------
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

# ---------- Auth: challenge ----------
class ChallengeResp(BaseModel):
    nonce: str
    aud: str
    ts: int

@app.get("/auth/challenge", response_model=ChallengeResp)
def challenge(aud: str = "mobile"):
    now = int(time.time())
    # sprzątaj stare
    for n, exp in list(NONCES.items()):
        if exp < now: NONCES.pop(n, None)
    nonce = os.urandom(16).hex()
    NONCES[nonce] = now + NONCE_TTL
    return {"nonce": nonce, "aud": aud, "ts": now}

# ---------- Admin: rejestracja klucza ----------
class PubReq(BaseModel):
    kid: str
    pub: str  # base64url(32B)

@app.post("/admin/register_pubkey")
def register_pubkey(req: PubReq):
    try:
        if len(b64u_decode(req.pub)) != 32:
            return {"ok": False, "reason": "bad-pubkey"}
    except Exception:
        return {"ok": False, "reason": "bad-pubkey"}
    PUBKEYS[req.kid] = req.pub
    return {"ok": True, "registered": list(PUBKEYS.keys())}

# ---------- Verify JWS ----------
class VerifyReq(BaseModel):
    jws: str
    kid: Optional[str] = None  # opcjonalnie (i tak czytamy z headera)

@app.post("/guardian/verify")
async def guardian_verify(req: VerifyReq):
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
        if abs(now - int(payload.get("ts", 0))) > NONCE_TTL:
            return {"ok": False, "reason": "nonce-expired"}

        aud = payload.get("aud")
        nonce = payload.get("nonce")
        if (not aud) or (not nonce):
            return {"ok": False, "reason": "missing-claims"}

        exp = NONCES.get(nonce, 0)
        if not exp or exp < now:
            return {"ok": False, "reason": "nonce-expired"}
        NONCES.pop(nonce, None)

        # session mint
        raw = (kid + nonce + str(payload["ts"])).encode() + os.urandom(4)
        sess = "sess_" + hashlib.blake2b(raw, digest_size=12).hexdigest()
        sess_exp = now + SESSION_TTL
        SESSIONS[sess] = {"kid": kid, "exp": sess_exp}

        frame = {"ts": now, "kid": kid, "vec": {"auth": "ok", "aud": aud}}
        asyncio.create_task(ws_manager.broadcast(frame))
        return {"ok": True, "payload": payload, "session": sess, "exp": sess_exp}
    except Exception as e:
        return {"ok": False, "reason": "server-error", "detail": str(e)}

# ---------- pomocnik: weryfikacja Bearer ----------
def read_bearer(auth: Optional[str]) -> Optional[str]:
    if not auth: return None
    if not auth.lower().startswith("bearer "): return None
    return auth.split(" ", 1)[1].strip()

def get_session_or_none(sess_id: str):
    now = int(time.time())
    ses = SESSIONS.get(sess_id)
    if not ses: return None
    if ses["exp"] < now:
        SESSIONS.pop(sess_id, None)
        return None
    return ses

# ---------- chroniona trasa (demo) ----------
@app.get("/protected/hello")
def protected_hello(authorization: Optional[str] = Header(None)):
    sess_id = read_bearer(authorization)
    if not sess_id: return {"ok": False, "reason": "no-bearer"}
    ses = get_session_or_none(sess_id)
    if not ses: return {"ok": False, "reason": "invalid-or-expired"}
    return {"ok": True, "msg": "hello dev-user", "kid": ses["kid"], "exp": ses["exp"]}

# ---------- refresh / logout ----------
@app.post("/auth/refresh")
def refresh(authorization: Optional[str] = Header(None)):
    sess_id = read_bearer(authorization)
    if not sess_id: return {"ok": False, "reason": "no-bearer"}
    ses = get_session_or_none(sess_id)
    if not ses: return {"ok": False, "reason": "invalid-or-expired"}
    now = int(time.time())
    ses["exp"] = now + SESSION_TTL
    return {"ok": True, "session": sess_id, "exp": ses["exp"]}

@app.post("/auth/logout")
def logout(authorization: Optional[str] = Header(None)):
    sess_id = read_bearer(authorization)
    if not sess_id: return {"ok": True}  # idempotent
    SESSIONS.pop(sess_id, None)
    return {"ok": True}

# ---------- Mobile signer ----------
MOBILE_HTML = """<!doctype html><meta charset="utf-8"><title>Guardian — Mobile Signer</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:16px}
 h1{margin:0 0 12px}
 .grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
 .card{border:1px solid #eee;border-radius:12px;padding:12px}
 textarea,input,button{font:inherit}
 textarea,input{width:100%;box-sizing:border-box;border:1px solid #ddd;border-radius:8px;padding:8px}
 button{border:1px solid #ddd;border-radius:8px;background:#fafafa;padding:8px 10px;cursor:pointer}
 pre{background:#f7f7f7;border:1px solid #eee;padding:8px;border-radius:8px;white-space:pre-wrap}
 .row{display:grid;grid-template-columns:1fr;gap:8px}
 small.mono{font-family:ui-monospace,Menlo,Consolas,monospace}
 .ok{color:#27ae60} .err{color:#c0392b}
</style>

<h1>Guardian — Mobile Signer</h1>
<div class="grid">
  <div class="card">
    <b>1) Klucz prywatny (PRIV, seed 32B)</b>
    <input id="kid" placeholder="kid" value="dev-key-1" style="margin:6px 0">
    <textarea id="priv" rows="3" placeholder="Wklej PRIV z terminala"></textarea>
    <div class="row"><small class="mono">PRIV to seed 32B w base64url (z terminala). Strona zapisuje go lokalnie w przeglądarce.</small>
    <button id="save">💾 Zapisz w przeglądarce</button></div>
  </div>

  <div class="card">
    <b>2) Rejestracja PUB</b>
    <button id="register">🕊️ Zarejestruj PUB na serwerze</button>
    <pre id="regOut"></pre>
  </div>

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

  <div class="card">
    <b>5) Token (Bearer)</b>
    <input id="tok" readonly>
    <div class="row">
      <button id="ping">🔓 Ping /protected/hello</button>
      <button id="refresh">🔄 Refresh</button>
      <button id="logout">🚪 Logout</button>
      <small>Wygasa za: <b id="ttl">-</b>s</small>
    </div>
    <pre id="pingOut"></pre>
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/tweetnacl@1.0.3/nacl.min.js"></script>
<script>
const $=id=>document.getElementById(id);
const enc=new TextEncoder(), dec=new TextDecoder();
const b64u = b => btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\\+/g,'-').replace(/\\//g,'_').replace(/=+$/,'');
const fromB64u = s => {
  s=s.replace(/-/g,'+').replace(/_/g,'/'); while(s.length%4)s+='=';
  const bin=atob(s), out=new Uint8Array(bin.length); for(let i=0;i<bin.length;i++) out[i]=bin.charCodeAt(i); return out;
};
const LS_PRIV="guardian_priv_seed", LS_SESS="guardian_session";

$("priv").value=localStorage.getItem(LS_PRIV)||"";
$("save").onclick=()=>{localStorage.setItem(LS_PRIV, $("priv").value.trim()); alert("Zapisano PRIV w przeglądarce");};

function getKeypair(){
  const seedB64=$("priv").value.trim(); if(!seedB64) throw new Error("Brak PRIV");
  const seed=fromB64u(seedB64); if(seed.length!==32) throw new Error("PRIV musi być 32B (base64url)");
  const kp=nacl.sign.keyPair.fromSeed(seed); return {publicKey:kp.publicKey, secretKey:kp.secretKey};
}

$("register").onclick=async()=>{
  try{
    const kp=getKeypair(); const pubB64=b64u(kp.publicKey); const kid=$("kid").value.trim()||"dev-key-1";
    const r=await fetch("/admin/register_pubkey",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({kid, pub:pubB64})});
    $("regOut").textContent=await r.text();
  }catch(e){$("regOut").textContent="ERR: "+e.message;}
};

let lastChallenge=null;
$("getCh").onclick=async()=>{
  const r=await fetch("/auth/challenge"); lastChallenge=await r.json(); $("chOut").textContent=JSON.stringify(lastChallenge,null,2);
};

function setSession(sess){
  localStorage.setItem(LS_SESS, JSON.stringify(sess));
  $("tok").value=sess.id; tick(); 
}
function getSession(){ try{return JSON.parse(localStorage.getItem(LS_SESS)||"null");}catch{return null;} }
function clearSession(){ localStorage.removeItem(LS_SESS); $("tok").value=""; $("ttl").textContent="-"; }

let tickTimer=null;
function tick(){
  if(tickTimer) clearInterval(tickTimer);
  tickTimer=setInterval(()=>{
    const s=getSession(); if(!s){$("ttl").textContent="-"; return;}
    const left=Math.max(0, Math.floor(s.exp - Date.now()/1000));
    $("ttl").textContent=left;
    if(left===0){ clearInterval(tickTimer); }
  },1000);
}

$("verify").onclick=async()=>{
  try{
    if(!lastChallenge) throw new Error("Najpierw pobierz challenge.");
    const kid=$("kid").value.trim()||"dev-key-1";
    const hdr={alg:"EdDSA",typ:"JWT",kid}; 
    const pld={aud:lastChallenge.aud, nonce:lastChallenge.nonce, ts: Math.floor(Date.now()/1000)};
    const h_b=b64u(enc.encode(JSON.stringify(hdr)));
    const p_b=b64u(enc.encode(JSON.stringify(pld)));
    const msg=enc.encode(h_b+"."+p_b);
    const kp=getKeypair(); const sig=nacl.sign.detached(msg, kp.secretKey); const jws=h_b+"."+p_b+"."+b64u(sig);
    const r=await fetch("/guardian/verify",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({jws})});
    const out=await r.json(); $("verOut").textContent=JSON.stringify(out,null,2);
    if(out.ok){
      setSession({id: out.session, exp: out.exp, kid});
    }
  }catch(e){$("verOut").textContent="ERR: "+e.message;}
};

$("ping").onclick=async()=>{
  const s=getSession(); if(!s){$("pingOut").textContent="Brak sesji"; return;}
  const r=await fetch("/protected/hello",{headers:{Authorization:"Bearer "+s.id}}); $("pingOut").textContent=await r.text();
};

$("refresh").onclick=async()=>{
  const s=getSession(); if(!s){$("pingOut").textContent="Brak sesji"; return;}
  const r=await fetch("/auth/refresh",{method:"POST",headers:{Authorization:"Bearer "+s.id}});
  const x=await r.json(); $("pingOut").textContent=JSON.stringify(x,null,2);
  if(x.ok){ setSession({id:s.id, exp:x.exp, kid:s.kid}); }
};

$("logout").onclick=async()=>{
  const s=getSession(); if(!s){clearSession(); $("pingOut").textContent="OK (no session)"; return;}
  const r=await fetch("/auth/logout",{method:"POST",headers:{Authorization:"Bearer "+s.id}});
  $("pingOut").textContent=await r.text(); clearSession();
};

const s0=getSession(); if(s0){ $("tok").value=s0.id; tick(); }
</script>
"""

@app.get("/mobile")
def mobile_page():
    return HTMLResponse(MOBILE_HTML)
