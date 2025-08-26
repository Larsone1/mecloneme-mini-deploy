import asyncio, json, time, os, base64
from typing import List, Dict, Any, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Header
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

# ==== Base64url helpers ========================================================

def b64u_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")

def b64u_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)

# ==== App & Config =============================================================

app = FastAPI(title="MeCloneMe API (mini)")

NONCE_TTL = int(os.getenv("NONCE_TTL", "300"))      # 5 min
SESSION_TTL = int(os.getenv("SESSION_TTL", "900"))  # 15 min
RATE_WINDOW = int(os.getenv("RATE_WINDOW", "10"))   # sekundy
RATE_MAX = int(os.getenv("RATE_MAX", "30"))         # max żądań / okno / IP

# Proste "pamięci" w RAM
NONCES: Dict[str, int] = {}                      # nonce -> expiry (ts)
PUBKEYS: Dict[str, str] = {}                     # kid -> pub (base64url 32B)
SESSIONS: Dict[str, Dict[str, Any]] = {}         # sess_id -> {kid, exp}
PROFILES: Dict[str, Dict[str, Any]] = {}         # kid -> {displayName, avatarColor}
RATE: Dict[str, List[int]] = {}                  # ip -> list[timestamps]

# ==== NaCl (Ed25519) ===========================================================
from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError

# ==== Pydantic models ===========================================================
class ChallengeResp(BaseModel):
    nonce: str
    aud: str
    ts: int

class VerifyReq(BaseModel):
    jws: str

class PubReq(BaseModel):
    kid: str
    pub: str  # base64url 32B ed25519

class ShadowFrame(BaseModel):
    ts: int
    kid: Optional[str] | None = None
    vec: Dict[str, Any] = {}

class Profile(BaseModel):
    displayName: Optional[str] = None
    avatarColor: Optional[str] = None

# ==== WebSocket live-log =======================================================
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
        text = json.dumps(data)
        stale: List[WebSocket] = []
        for ws in list(self.active):
            try:
                await ws.send_text(text)
            except Exception:
                stale.append(ws)
        for ws in stale:
            await self.disconnect(ws)

ws_manager = WSManager()

@app.websocket("/shadow/ws")
async def shadow_ws(ws: WebSocket):
    await ws_manager.connect(ws)
    try:
        # czytamy, ale nic nie robimy z wejściem
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        await ws_manager.disconnect(ws)

@app.post("/shadow/ingest")
async def shadow_ingest(frame: ShadowFrame):
    await ws_manager.broadcast(frame.dict())
    return {"ok": True}

# ==== Mini panel (desktop) =====================================================
PANEL_HTML = """<!doctype html><meta charset=utf-8><title>Guardian – mini panel</title>
<body style=font-family:system-ui,ui-sans-serif,sans-serif;margin:16px>
<h1>Guardian — mini panel</h1>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
  <div style="border:1px solid #eee;border-radius:12px;padding:12px">
    <h2>Challenge</h2>
    <pre id=challenge style="background:#f7f7f7;border:1px solid #eee;padding:12px;border-radius:8px">...</pre>
  </div>
  <div style="border:1px solid #eee;border-radius:12px;padding:12px">
    <h2>Live log <span id=wsState style="color:#888">WS: connecting…</span></h2>
    <pre id=log style="background:#f7f7f7;border:1px solid #eee;padding:12px;height:260px;overflow:auto;border-radius:8px"></pre>
  </div>
</div>

<div style="margin-top:24px;border:1px solid #eee;border-radius:12px;padding:12px">
  <h2>Postęp projektu (tylko lokalnie — zapis w przeglądarce)</h2>
  <div id=bars></div>
  <button id=save>💾 Zapisz</button> <button id=reset>↺ Reset</button>
</div>

<script>
const $ = (id)=>document.getElementById(id);
fetch('/auth/challenge').then(r=>r.json()).then(x=>{
  $('challenge').textContent = JSON.stringify(x,null,2);
}).catch(()=>{$('challenge').textContent = 'API offline';});

// Live WS
const state = $('wsState');
let ws = new WebSocket((location.protocol==='https:'?'wss':'ws')+'://'+location.host+'/shadow/ws');
ws.onopen = ()=>{ state.textContent='WS: connected'; state.style.color='#0a0'; logMsg({vec:{sys:'ws-connected'}, ts:Math.floor(Date.now()/1000)}); };
ws.onclose = ()=>{ state.textContent='WS: closed'; state.style.color='#a00'; };
ws.onmessage = (e)=>{ try { logMsg(JSON.parse(e.data)); } catch{} };
function logMsg(m){ const log=$('log'); log.textContent += JSON.stringify(m)+"\n"; log.scrollTop = log.scrollHeight; }

// Pasek postępu (lekki)
const ITEMS = [
  ['Guardian/Auth','auth'],
  ['AR Engine (R&D)','ar'],
  ['App Shell / UI','ui'],
  ['Cloud & Deploy','cloud'],
  ['MVP (całość)','mvp']
];
const storeKey='progress.v1';
let data = JSON.parse(localStorage.getItem(storeKey) || '{}');
function render(){
  const wrap=$('bars'); wrap.innerHTML='';
  ITEMS.forEach(([label,key])=>{
    const val = (data[key]??0)|0; const id='val_'+key;
    const row=document.createElement('div'); row.style.display='grid'; row.style.gridTemplateColumns='160px 1fr 40px 40px'; row.style.alignItems='center'; row.style.gap='8px'; row.style.margin='6px 0';
    row.innerHTML=`<div>${label}</div><div style="background:#eee;height:8px;border-radius:6px;overflow:hidden"><div style="height:8px;background:#2ea043;width:${val}%"></div></div><input id="${id}" value="${val}" size=2 /><div>${val}%</div>`;
    wrap.appendChild(row);
  });
}
render();
$('save').onclick=()=>{ ITEMS.forEach(([_,k])=>{ const v=+document.getElementById('val_'+k).value||0; data[k]=Math.max(0,Math.min(100,v)); }); localStorage.setItem(storeKey,JSON.stringify(data)); render(); };
$('reset').onclick=()=>{ data={}; localStorage.removeItem(storeKey); render(); };
</script>
</body>"""

@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(PANEL_HTML)

# ==== /auth/challenge ==========================================================
@app.get("/auth/challenge", response_model=ChallengeResp)
def challenge(aud: str = "mobile"):
    now = int(time.time())
    # sprzątaj stare nonces
    for n, exp in list(NONCES.items()):
        if exp < now:
            NONCES.pop(n, None)
    # wygeneruj i zapamiętaj
    nonce = os.urandom(16).hex()
    NONCES[nonce] = now + NONCE_TTL
    return {"nonce": nonce, "aud": aud, "ts": now}

# ==== Admin: rejestracja klucza publicznego (demo/dev) =========================
@app.post("/admin/register_pubkey")
async def register_pubkey(req: PubReq):
    try:
        if len(b64u_decode(req.pub)) != 32:
            return {"ok": False, "reason": "bad-pubkey"}
    except Exception:
        return {"ok": False, "reason": "bad-pubkey"}
    PUBKEYS[req.kid] = req.pub
    return {"ok": True, "registered": list(PUBKEYS.keys())}

# ==== Rate limiting (IP) =======================================================

def rate_ok(ip: str) -> bool:
    now = int(time.time())
    bucket = RATE.setdefault(ip, [])
    # drop stare
    while bucket and bucket[0] < now - RATE_WINDOW:
        bucket.pop(0)
    if len(bucket) >= RATE_MAX:
        return False
    bucket.append(now)
    return True

# ==== /guardian/verify (JWS Ed25519) ===========================================
@app.post("/guardian/verify")
async def guardian_verify(req: VerifyReq, request: Request):
    # rate limit
    ip = (request.client.host if request.client else "?")
    if not rate_ok(ip):
        return {"ok": False, "reason": "rate-limit"}

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

        # checks app-level
        now = int(time.time())
        if abs(now - int(payload.get("ts", 0))) > NONCE_TTL:
            return {"ok": False, "reason": "nonce-expired"}
        aud = payload.get("aud")
        nonce = payload.get("nonce")
        if (not aud) or (not nonce):
            return {"ok": False, "reason": "missing-claims"}
        exp = NONCES.get(nonce)
        if not exp or exp < now:
            return {"ok": False, "reason": "nonce-expired"}
        NONCES.pop(nonce, None)  # jednorazowy

        # success → utwórz sesję
        sess = "sess_" + os.urandom(16).hex()
        sess_exp = now + SESSION_TTL
        SESSIONS[sess] = {"kid": kid, "exp": sess_exp}

        frame = {"ts": now, "kid": kid, "vec": {"auth": "ok", "aud": aud}}
        asyncio.create_task(ws_manager.broadcast(frame))
        return {"ok": True, "payload": payload, "session": sess, "exp": sess_exp}
    except Exception as e:
        return {"ok": False, "reason": "server-error", "detail": str(e)}

# ==== Session helpers & protected endpoints ====================================

def bearer_token(auth: Optional[str]) -> Optional[str]:
    if not auth: return None
    parts = auth.split()
    if len(parts) == 2 and parts[0].lower()=="bearer":
        return parts[1]
    return None


def require_session(token: Optional[str]):
    if not token: return None
    s = SESSIONS.get(token)
    if not s: return None
    now = int(time.time())
    if s["exp"] < now:  # expired
        SESSIONS.pop(token, None)
        return None
    return s

@app.get("/protected/hello")
async def protected_hello(Authorization: Optional[str] = Header(default=None)):
    tok = bearer_token(Authorization)
    s = require_session(tok)
    if not s:
        return {"ok": False, "reason": "unauthorized"}
    return {"ok": True, "msg": "hello dev-user", "kid": s["kid"], "exp": s["exp"]}

@app.post("/session/refresh")
async def session_refresh(Authorization: Optional[str] = Header(default=None)):
    tok = bearer_token(Authorization)
    s = require_session(tok)
    if not s:
        return {"ok": False, "reason": "unauthorized"}
    s["exp"] = int(time.time()) + SESSION_TTL
    return {"ok": True, "exp": s["exp"]}

@app.post("/session/logout")
async def session_logout(Authorization: Optional[str] = Header(default=None)):
    tok = bearer_token(Authorization)
    if tok and tok in SESSIONS:
        SESSIONS.pop(tok, None)
    return {"ok": True}

# ==== Lightweight profile (/me) ================================================
@app.get("/me")
async def get_me(Authorization: Optional[str] = Header(default=None)):
    tok = bearer_token(Authorization)
    s = require_session(tok)
    if not s:
        return {"ok": False, "reason": "unauthorized"}
    kid = s["kid"]
    prof = PROFILES.get(kid) or {"displayName": "dev-user", "avatarColor": "#2ea043"}
    return {"ok": True, "kid": kid, "profile": prof}

@app.post("/me")
async def set_me(p: Profile, Authorization: Optional[str] = Header(default=None)):
    tok = bearer_token(Authorization)
    s = require_session(tok)
    if not s:
        return {"ok": False, "reason": "unauthorized"}
    kid = s["kid"]
    cur = PROFILES.get(kid, {})
    if p.displayName is not None:
        cur["displayName"] = p.displayName
    if p.avatarColor is not None:
        cur["avatarColor"] = p.avatarColor
    PROFILES[kid] = cur
    return {"ok": True, "profile": cur}

# ==== Mobile page (Signer) =====================================================
MOBILE_HTML = """<!doctype html><meta charset=utf-8><title>Guardian — Mobile Signer</title>
<meta name=viewport content="width=device-width,initial-scale=1">
<style>
body{font-family:ui-sans-serif,system-ui;margin:16px;line-height:1.35}
h1{font-size:22px;margin:0 0 12px}
.row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.card{border:1px solid #eee;border-radius:12px;padding:12px}
textarea,input,button{width:100%;padding:10px;border:1px solid #ddd;border-radius:8px}
pre{background:#f7f7f7;border:1px solid #eee;border-radius:8px;padding:10px;white-space:pre-wrap;color:#333;font-size:13px}
button{cursor:pointer}
small{color:#666}
</style>
<h1>Guardian — Mobile Signer</h1>
<div class=row>
  <div class=card>
    <b>1) Klucz prywatny (PRIV, seed 32B)</b>
    <input id=kid placeholder=\"kid\" value=\"dev-key-1\" style=\"margin:8px 0\">
    <textarea id=priv rows=3 placeholder=\"Wklej PRIV z terminala\"></textarea>
    <div class=muted><small>PRIV to seed 32B w base64url (z terminala). Strona zapisuje go lokalnie w przeglądarce.</small></div>
    <button id=save style=\"margin-top:8px\">💾 Zapisz w przeglądarce</button>
  </div>
  <div class=card>
    <b>2) Rejestracja PUB</b>
    <button id=register>🕊️ Zarejestruj PUB na serwerze</button>
    <pre id=regOut></pre>
  </div>
</div>

<div class=row style=\"margin-top:12px\">
  <div class=card>
    <b>3) Challenge</b>
    <button id=getCh>🎯 Pobierz /auth/challenge</button>
    <pre id=chOut></pre>
  </div>
  <div class=card>
    <b>4) Podpisz JWS i zweryfikuj</b>
    <button id=verify>🔐 Podpisz & /guardian/verify</button>
    <pre id=verOut></pre>
  </div>
</div>

<div class=card style=\"margin-top:12px\">
  <b>5) Token (Bearer)</b>
  <input id=tok value=\"\" readonly>
  <div style=\"display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-top:8px\">
    <button id=ping>🔒 Ping /protected/hello</button>
    <button id=refresh>🔄 Refresh</button>
    <button id=logout>🚪 Logout</button>
  </div>
  <div style=\"margin-top:6px\">Wygasa za: <span id=expLeft>—</span></div>
  <pre id=pingOut></pre>
</div>

<script src="https://cdn.jsdelivr.net/npm/tweetnacl@1.0.3/nacl.min.js"></script>
<script>
const $ = id=>document.getElementById(id);
const enc = new TextEncoder();
function b64u(b){ return btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,''); }
function fromB64u(s){ s=s.replace(/-/g,'+').replace(/_/g,'/'); while(s.length%4) s+='='; const bin=atob(s), out=new Uint8Array(bin.length); for(let i=0;i<bin.length;i++) out[i]=bin.charCodeAt(i); return out; }

const LS_KEY='guardian_priv_seed';
$("priv").value = localStorage.getItem(LS_KEY)||'';
$("save").onclick=()=>{ localStorage.setItem(LS_KEY, $("priv").value.trim()); alert('Zapisano PRIV w przeglądarce.'); };

function getKeypair(){
  const seedB = fromB64u($("priv").value.trim());
  if(seedB.length!==32) throw new Error('PRIV musi być 32B (base64url)');
  return nacl.sign.keyPair.fromSeed(seedB);
}

let lastChallenge=null;
$("register").onclick= async ()=>{
  try{
    const kp=getKeypair(); const pub=b64u(kp.publicKey); const kid=$("kid").value.trim()||'dev-key-1';
    const r=await fetch('/admin/register_pubkey',{method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({kid, pub})});
    $("regOut").textContent = await r.text();
  }catch(e){ $("regOut").textContent='ERR: '+e.message; }
};
$("getCh").onclick= async ()=>{ const r=await fetch('/auth/challenge'); lastChallenge=await r.json(); $("chOut").textContent=JSON.stringify(lastChallenge,null,2); };

function startExpiryCountdown(exp){
  const box=$("expLeft");
  clearInterval(window.__expTimer);
  function tick(){ const left=Math.max(0, exp - Math.floor(Date.now()/1000)); box.textContent = left+'s'; if(left<=0) clearInterval(window.__expTimer); }
  tick(); window.__expTimer = setInterval(tick,1000);
}

$("verify").onclick= async ()=>{
  try{
    if(!lastChallenge) throw new Error('Najpierw pobierz challenge.');
    const kid=$("kid").value.trim()||'dev-key-1';
    const hdr={alg:'EdDSA',typ:'JWT',kid};
    const pld={aud:lastChallenge.aud, nonce:lastChallenge.nonce, ts:Math.floor(Date.now()/1000)};
    const h_b=b64u(enc.encode(JSON.stringify(hdr))); const p_b=b64u(enc.encode(JSON.stringify(pld)));
    const kp=getKeypair(); const msg=enc.encode(h_b+'.'+p_b); const sig=nacl.sign.detached(msg,kp.secretKey); const jws=h_b+'.'+p_b+'.'+b64u(sig);
    const r=await fetch('/guardian/verify',{method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({jws})});
    const x=await r.json(); $("verOut").textContent=JSON.stringify(x,null,2);
    if(x.ok){ $("tok").value=x.session; startExpiryCountdown(x.exp); }
  }catch(e){ $("verOut").textContent='ERR: '+e.message; }
};

async function authed(path){
  const t=$("tok").value.trim(); if(!t){ return {ok:false, reason:'no-token'}; }
  const r=await fetch(path,{headers:{'Authorization':'Bearer '+t}}); return r.json();
}
$("ping").onclick= async ()=>{ const x=await authed('/protected/hello'); $("pingOut").textContent=JSON.stringify(x,null,2); };
$("refresh").onclick= async ()=>{ const t=$("tok").value.trim(); if(!t) return; const r=await fetch('/session/refresh',{method:'POST', headers:{'Authorization':'Bearer '+t}}); const x=await r.json(); $("pingOut").textContent=JSON.stringify(x,null,2); if(x.ok) startExpiryCountdown(x.exp); };
$("logout").onclick= async ()=>{ const t=$("tok").value.trim(); if(!t) return; const r=await fetch('/session/logout',{method:'POST', headers:{'Authorization':'Bearer '+t}}); const x=await r.json(); $("pingOut").textContent=JSON.stringify(x,null,2); $("tok").value=''; $("expLeft").textContent='—'; };
</script>
"""

@app.get("/mobile")
def mobile_page():
    return HTMLResponse(MOBILE_HTML)
