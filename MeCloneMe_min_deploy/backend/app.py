from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import os, time, json, asyncio, base64, hashlib

# ---- Ed25519 (NaCl)
from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError

app = FastAPI(title="MeCloneMe API (mini)")

# --- Mini panel (tak jak wcześniej)
PANEL_HTML = """<!doctype html><meta charset="utf-8"><title>Guardian Mini Panel</title>
<body style="font-family: -apple-system, system-ui, sans-serif; padding:16px">
  <h1>Guardian — mini panel</h1>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
    <div style="border:1px solid #eee;border-radius:12px;padding:12px">
      <h2>Challenge</h2>
      <pre id="challenge" style="background:#f7f7f7;padding:12px;border-radius:8px">...</pre>
    </div>
    <div style="border:1px solid #eee;border-radius:12px;padding:12px">
      <h2>Live log</h2>
      <pre id="log" style="background:#f7f7f7;padding:12px;border-radius:8px;height:260px;overflow:auto"></pre>
    </div>
  </div>
  <script>
    fetch('/auth/challenge').then(r=>r.json()).then(x=>{
      document.getElementById('challenge').textContent = JSON.stringify(x,null,2);
      window.__nonce = x.nonce; // ułatwienie do testów
    }).catch(()=>{document.getElementById('challenge').textContent='API offline';});
    const log = document.getElementById('log');
    try{
      const ws = new WebSocket((location.protocol==='https:'?'wss':'ws')+'://'+location.host+'/shadow/ws');
      ws.onmessage = e => { try{
        const m = JSON.parse(e.data);
        if(m.vec){ log.textContent += JSON.stringify(m)+'\\n'; log.scrollTop = log.scrollHeight; }
      }catch{} };
    }catch(e){ log.textContent = 'WS error: '+e; }
  </script>
</body>"""

@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(PANEL_HTML)

# ---- CHALLENGE
class ChallengeResp(BaseModel):
    nonce: str
    aud: str
    ts: int

# prosta „pamięć” nounce → expiry (60s)
NONCES = {}
NONCE_TTL = 60

@app.get("/auth/challenge", response_model=ChallengeResp)
def challenge(aud: str = "mobile"):
    now = int(time.time())
    # generuj nonce i zapamiętaj
    nonce = os.urandom(16).hex()
    NONCES[nonce] = now + NONCE_TTL
    # sprzątaj stare
    for n, exp in list(NONCES.items()):
        if exp < now: NONCES.pop(n, None)
    return {"nonce": nonce, "aud": aud, "ts": now}

# ---- JWS weryfikacja Ed25519
def b64u_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

def b64u_decode(s: str) -> bytes:
    pad = '=' * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)

# mapa KID -> publiczny klucz (base64url 32B), rejestrowana przez /admin/register_pubkey
PUBKEYS: dict[str,str] = {}

class VerifyReq(BaseModel):
    jws: str

@app.post("/guardian/verify")
def guardian_verify(req: VerifyReq):
    """
    Oczekujemy compact JWS: header.payload.signature (base64url),
    gdzie alg=EdDSA i podpis to Ed25519.
    """
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

        # proste sprawdzenia aplikacyjne
        now = int(time.time())
        if abs(now - int(payload.get("ts", 0))) > 120:
            return {"ok": False, "reason": "stale-ts"}

        aud = payload.get("aud")
        nonce = payload.get("nonce")
        if (not aud) or (not nonce):
            return {"ok": False, "reason": "missing-claims"}

        # nonce musi istnieć i nie być przeterminowany
        exp = NONCES.get(nonce)
        if not exp or exp < now:
            return {"ok": False, "reason": "nonce-expired"}

        # nonce zużyty – usuń
        NONCES.pop(nonce, None)

        # sukces: wyślij do Live log
        frame = {"ts": now, "kid": kid, "vec": {"auth":"ok","aud":aud}}
        asyncio.create_task(ws_manager.broadcast(frame))
        return {"ok": True, "payload": payload}
    except Exception as e:
        return {"ok": False, "reason": "server-error", "detail": str(e)}

# ---- Admin: rejestracja klucza publicznego (dev/demo)
class PubReq(BaseModel):
    kid: str
    pub: str  # base64url(32B ed25519)

@app.post("/admin/register_pubkey")
def register_pubkey(req: PubReq):
    if not req.kid or not req.pub: return {"ok": False}
    # minimalna walidacja długości
    try:
        if len(b64u_decode(req.pub)) != 32:
            return {"ok": False, "reason": "bad-pubkey"}
    except Exception:
        return {"ok": False, "reason": "bad-pubkey"}
    PUBKEYS[req.kid] = req.pub
    return {"ok": True, "registered": list(PUBKEYS.keys())}

# --- WebSocket manager (prosta wersja bez locka)
class WSManager:
    def __init__(self):
        self.active = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    async def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, data: dict):
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


@app.websocket("/shadow/ws")
async def shadow_ws(ws: WebSocket):
    await ws_manager.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        await ws_manager.disconnect(ws)

class ShadowFrame(BaseModel):
    ts: int
    kid: str | None = None
    vec: dict = {}

@app.post("/shadow/ingest")
async def shadow_ingest(frame: ShadowFrame):
    os.makedirs("logs", exist_ok=True)
    with open("logs/shadow.jsonl", "a") as f:
        f.write(json.dumps(frame.dict()) + "\n")
    await ws_manager.broadcast(frame.dict())
    return {"ok": True}

@app.get("/mobile")
def mobile_page():
    return HTMLResponse("""
<!doctype html>
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Guardian – Mobile Signer</title>
<style>
  body{font-family:ui-sans-serif,system-ui;margin:16px;line-height:1.4}
  h1{font-size:20px;margin:0 0 12px}
  .row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
  .card{border:1px solid #eee;border-radius:12px;padding:12px}
  textarea,input,button{width:100%;padding:8px;border:1px solid #ddd;border-radius:8px}
  pre{background:#f7f7f7;border:1px solid #eee;border-radius:8px;padding:8px;white-space:pre-wrap}
  .muted{color:#666;font-size:12px}
</style>
<h1>Guardian — Mobile Signer</h1>

<div class="row">
  <div class="card">
    <b>1) Klucz prywatny (PRIV, seed 32B)</b>
    <input id="kid" placeholder="kid" value="dev-key-1" style="margin:8px 0">
    <textarea id="priv" rows="3" placeholder="Wklej PRIV z terminala"></textarea>
    <div class="muted">PRIV to seed 32B w base64url (z terminala). Strona zapisuje go lokalnie w przeglądarce.</div>
    <button id="save" style="margin-top:8px">💾 Zapisz w przeglądarce</button>
  </div>
  <div class="card">
    <b>2) Rejestracja PUB</b>
    <button id="register">📡 Zarejestruj PUB na serwerze</button>
    <pre id="regOut"></pre>
  </div>
</div>

<div class="row" style="margin-top:12px">
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
</div>

<script src="https://cdn.jsdelivr.net/npm/tweetnacl@1.0.3/nacl.min.js"></script>
<script>
const enc = new TextEncoder(), dec = new TextDecoder();
const b64u = b => btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\\+/g,'-').replace(/\\//g,'_').replace(/=+$/,'');
const fromB64u = s => {
  s = s.replace(/-/g,'+').replace(/_/g,'/'); while(s.length%4) s+='=';
  const bin = atob(s), out = new Uint8Array(bin.length); for(let i=0;i<bin.length;i++) out[i]=bin.charCodeAt(i); return out;
};

const LS_KEY = "guardian_priv_seed";
const $ = id => document.getElementById(id);
$("priv").value = localStorage.getItem(LS_KEY)||"";

$("save").onclick = () => {
  localStorage.setItem(LS_KEY, $("priv").value.trim());
  alert("Zapisano PRIV w przeglądarce.");
};

function getKeypair(){
  const seed = $("priv").value.trim();
  if(!seed) throw new Error("Brak PRIV");
  const seedBytes = fromB64u(seed);           // 32B
  if(seedBytes.length !== 32) throw new Error("PRIV musi być 32B (base64url)");
  return nacl.sign.keyPair.fromSeed(seedBytes); // {publicKey(32B), secretKey(64B)}
}

$("register").onclick = async () => {
  try{
    const kp = getKeypair();
    const pubB64u = b64u(kp.publicKey);
    const kid = $("kid").value.trim() || "dev-key-1";
    const resp = await fetch("/admin/register_pubkey", {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify({kid, pub: pubB64u})
    });
    $("regOut").textContent = await resp.text();
  }catch(e){ $("regOut").textContent = "ERR: "+e.message; }
};

let lastChallenge = null;
$("getCh").onclick = async () => {
  const r = await fetch("/auth/challenge");
  lastChallenge = await r.json();
  $("chOut").textContent = JSON.stringify(lastChallenge,null,2);
};

$("verify").onclick = async () => {
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

    const r = await fetch("/guardian/verify", {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify({jws})
    });
    $("verOut").textContent = await r.text();
  }catch(e){ $("verOut").textContent = "ERR: "+e.message; }
};
</script>
""")
