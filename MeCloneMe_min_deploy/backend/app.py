import os, time, json, base64, secrets, io, re
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError

# ===== Base64url helpers =====
def b64u_decode(s: str) -> bytes:
    s = s or ""
    s += "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s.encode())

def b64u_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")

# ===== Config (ENV) =====
API_VERSION = os.getenv("API_VERSION", "0.3.2")
NONCE_TTL   = int(os.getenv("NONCE_TTL", "300"))
SESSION_TTL = int(os.getenv("SESSION_TTL", "3600"))
P95_WARN    = int(os.getenv("P95_WARN", "300"))
P95_CRIT    = int(os.getenv("P95_CRIT", "800"))
RATE_WINDOW = int(os.getenv("RATE_WINDOW", "60"))
RATE_MAX    = int(os.getenv("RATE_MAX", "120"))

# ===== Models =====
class PubKey(BaseModel):
    kid: str
    key: str  # base64url-encoded public key (Ed25519)

class Challenge(BaseModel):
    aud: str
    nonce: str
    ts: int

class SignedJWS(BaseModel):
    jws: str

class SessionInfo(BaseModel):
    kid: str
    sid: str
    exp: int

class Snapshot(BaseModel):
    tag: str
    payload: Dict[str, Any]

class Alert(BaseModel):
    level: str  # ok|warn|crit
    msg: str
    ts: int
    extra: Optional[Dict[str, Any]] = None

# ===== App =====
app = FastAPI(title="MeCloneMe Mini API", version=API_VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===== Stores =====
PUBKEYS: Dict[str, str] = {}              # kid -> base64url(pubkey)
SESSIONS: Dict[str, Dict[str, Any]] = {}  # sid -> {kid, exp}
RATE: Dict[str, List[int]] = {}           # ip -> [timestamps]
ALERTS: List[Dict[str, Any]] = []         # rolling alerts

# ===== Simple persistence =====
DATA_DIR = "data"
LOG_DIR = "logs"
SNAPS_DIR = os.path.join(LOG_DIR, "snaps")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(SNAPS_DIR, exist_ok=True)
# ===== Lightweight tuning (Render-friendly) =====
METRICS_TTL = float(os.getenv("METRICS_TTL", "2"))  # seconds to cache /metrics
AUDIT_SAMPLE_N = int(os.getenv("AUDIT_SAMPLE_N", "1"))  # 1 = log every request; 10 = ~10% sampled
SKIP_AUDIT_PATHS = set((os.getenv("SKIP_AUDIT_PATHS", "/metrics,/health,/ar/ping").split(",")))
METRICS_CACHE = {"ts": 0.0, "body": ""}
PUBKEYS_PATH = os.path.join(DATA_DIR, "pubkeys.json")
SESSIONS_PATH = os.path.join(DATA_DIR, "sessions.json")
EVENTS_JSONL = os.path.join(LOG_DIR, "events.jsonl")
SHADOW_JSONL = os.path.join(LOG_DIR, "shadow.jsonl")

BOOT_TS = int(time.time())
REQ_COUNT = 0
LAST_ALERT_TS: Dict[str, int] = {}  # throttling

# ===== Utils =====
def _load_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def _save_json(path: str, obj: Any):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f)
    except Exception:
        pass

def write_event(kind: str, **fields):
    fields["kind"] = kind
    fields["ts"] = int(time.time())
    try:
        with open(EVENTS_JSONL, "a", encoding="utf-8") as f:
            f.write(json.dumps(fields, ensure_ascii=False) + "\n")
    except Exception:
        pass

def write_shadow(**fields):
    fields["ts"] = int(time.time())
    try:
        with open(SHADOW_JSONL, "a", encoding="utf-8") as f:
            f.write(json.dumps(fields, ensure_ascii=False) + "\n")
    except Exception:
        pass


def tail_jsonl(path: str, n: int) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f.readlines()[-n:]:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    except Exception:
        pass
    return rows


def load_stores():
    global PUBKEYS, SESSIONS
    PUBKEYS = _load_json(PUBKEYS_PATH, {})
    SESSIONS = _load_json(SESSIONS_PATH, {})

def save_sessions():
    _save_json(SESSIONS_PATH, SESSIONS)

# ===== Nonces =====
NONCES: Dict[str, int] = {}  # nonce -> exp

def new_nonce(aud: str) -> Challenge:
    nonce = base64.urlsafe_b64encode(secrets.token_bytes(24)).decode().rstrip("=")
    ts = int(time.time())
    NONCES[nonce] = ts + NONCE_TTL
    return Challenge(aud=aud, nonce=nonce, ts=ts)

# ===== Sessions =====

def new_session(kid: str) -> SessionInfo:
    sid = base64.urlsafe_b64encode(secrets.token_bytes(18)).decode().rstrip("=")
    exp = int(time.time()) + SESSION_TTL
    SESSIONS[sid] = {"kid": kid, "exp": exp}
    save_sessions()
    return SessionInfo(kid=kid, sid=sid, exp=exp)

def get_session(sid: str) -> Optional[SessionInfo]:
    s = SESSIONS.get(sid)
    if not s:
        return None
    if s["exp"] < int(time.time()):
        SESSIONS.pop(sid, None)
        save_sessions()
        return None
    return SessionInfo(kid=s["kid"], sid=sid, exp=s["exp"])

# ===== Rate limiting =====

def rate_allow(ip: str) -> bool:
    now = int(time.time())
    buf = RATE.get(ip, [])
    buf = [t for t in buf if t > now - RATE_WINDOW]
    allowed = len(buf) < RATE_MAX
    buf.append(now)
    RATE[ip] = buf
    return allowed


def rate_is_hot(ip: str) -> Optional[int]:
    """Return current count if window usage >= 80% of limit."""
    cnt = len(RATE.get(ip, []))
    return cnt if cnt >= int(RATE_MAX * 0.8) else None

# ===== WS (echo stub) =====
@app.websocket("/ws/echo")
async def ws_echo(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            msg = await ws.receive_text()
            await ws.send_text(msg)
    except WebSocketDisconnect:
        pass

# ===== HTTP middleware: audit + metrics =====
@app.middleware("http")
async def audit_mw(request: Request, call_next):
    global REQ_COUNT
    t0 = time.perf_counter()
    ip = request.client.host if request.client else "?"
    ua = (request.headers.get("user-agent") or "")[:160]
    path = request.url.path
    method = request.method
    status = 500
    try:
        response = await call_next(request)
        status = getattr(response, "status_code", 200)
        return response
    finally:
        ms = int((time.perf_counter() - t0) * 1000)
        REQ_COUNT += 1
        reqno = REQ_COUNT
        # Fast bypass for light paths
        if path in SKIP_AUDIT_PATHS:
            return
        # Sampling to reduce IO/CPU
        if AUDIT_SAMPLE_N > 1 and (reqno % AUDIT_SAMPLE_N) != 0:
            return
        write_event("http", ip=ip, ua=ua, method=method, path=path, status=status, ms=ms)
        if status >= 500:
            await emit_alert("crit", "HTTP 5xx", cooldown_s=10, path=path, code=status)
        hot = rate_is_hot(ip)
        if hot is not None:
            await emit_alert("warn", "Rate hot", cooldown_s=15, ip=ip, count=hot, limit=RATE_MAX)

# ===== Percentiles (robust) =====

def _percentile(values: List[int], p: float) -> int:
    if not values:
        return 0
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    k = (len(xs) - 1) * p
    f = int(k)
    c = min(f + 1, len(xs) - 1)
    if f == c:
        return xs[f]
    d0 = xs[f] * (c - k)
    d1 = xs[c] * (k - f)
    return int(d0 + d1)


def _lat_stats(vals: List[int]) -> Dict[str, int]:
    if not vals:
        return {"p50": 0, "p95": 0, "avg": 0, "max": 0}
    return {
        "p50": _percentile(vals, 0.50),
        "p95": _percentile(vals, 0.95),
        "avg": int(sum(vals) / len(vals)),
        "max": max(vals),
    }

# ===== Metrics builders =====

def _compute_summary(n: int) -> Dict[str, Any]:
    rows = [r for r in tail_jsonl(EVENTS_JSONL, n) if r.get("kind") == "http"]
    total = len(rows)
    by_path: Dict[str, int] = {}
    by_method: Dict[str, int] = {}
    by_status: Dict[str, int] = {}
    lat: List[int] = []
    for r in rows:
        p = r.get("path", "?")
        by_path[p] = by_path.get(p, 0) + 1
        m = r.get("method", "?")
        by_method[m] = by_method.get(m, 0) + 1
        s = str(r.get("status", "?"))
        by_status[s] = by_status.get(s, 0) + 1
        if isinstance(r.get("ms"), int):
            lat.append(r["ms"])
    top_paths = sorted(by_path.items(), key=lambda x: x[1], reverse=True)[:5]
    lat_q = _lat_stats(lat)
    err4 = sum(v for k, v in by_status.items() if k.startswith("4"))
    err5 = sum(v for k, v in by_status.items() if k.startswith("5"))
    return {
        "ok": True,
        "total": total,
        "paths_top5": top_paths,
        "methods": by_method,
        "statuses": by_status,
        "errors": {"4xx": err4, "5xx": err5},
        "latency_ms": lat_q,
    }


def _compute_series(minutes: int = 30) -> Dict[str, Any]:
    rows = [r for r in tail_jsonl(EVENTS_JSONL, minutes * 60) if r.get("kind") == "http"]
    buckets: Dict[int, List[int]] = {}
    counts: Dict[int, int] = {}
    now = int(time.time())
    floor_start = now - minutes * 60
    for r in rows:
        t = r.get("ts", now)
        m = (t // 60) * 60
        ms = r.get("ms")
        if isinstance(ms, int):
            buckets.setdefault(m, []).append(ms)
        counts[m] = counts.get(m, 0) + 1
    series = []
    for m in range((floor_start // 60) * 60, (now // 60) * 60 + 60, 60):
        vals = buckets.get(m, [])
        q = _lat_stats(vals) if vals else {"p50": 0, "p95": 0, "avg": 0, "max": 0}
        series.append({"t": m, "p95": q["p95"], "count": counts.get(m, 0)})
    return {"ok": True, "series": series[-minutes:]}


def _compute_health(n: int) -> Dict[str, Any]:
    rows = [r for r in tail_jsonl(EVENTS_JSONL, n) if r.get("kind") == "http"]
    lat = [r["ms"] for r in rows if isinstance(r.get("ms"), int)]
    lat_q = _lat_stats(lat)
    codes: Dict[str, int] = {}
    for r in rows:
        k = str(r.get("status", "?"))
        codes[k] = codes.get(k, 0) + 1
    level = "ok"
    if lat_q["p95"] >= P95_CRIT:
        level = "crit"
    elif lat_q["p95"] >= P95_WARN:
        level = "warn"
    return {
        "ok": True,
        "ts": int(time.time()),
        "version": API_VERSION,
        "uptime_s": int(time.time()) - BOOT_TS,
        "req_count": REQ_COUNT,
        "rate_window_s": RATE_WINDOW,
        "latency_ms": lat_q,
        "codes": codes,
        "sample_size": len(rows),
        "level": level,
        "thresholds": {"warn": P95_WARN, "crit": P95_CRIT},
    }

# ===== UI roots =====
@app.get("/", response_class=HTMLResponse)
def root():
    return HTMLResponse(PANEL_HTML)

@app.get("/mobile", response_class=HTMLResponse)
def mobile_page():
    return HTMLResponse(MOBILE_HTML)

# ===== Simple health =====
@app.get("/api/health")
def health():
    return {"ok": True, "version": API_VERSION, "ts": int(time.time())}

# ===== Pubkeys API =====
@app.get("/auth/keys")
def list_keys():
    return {"ok": True, "keys": [{"kid": k, "key": v} for k, v in PUBKEYS.items()]}

@app.post("/auth/keys")
def add_key(k: PubKey):
    PUBKEYS[k.kid] = k.key
    _save_json(PUBKEYS_PATH, PUBKEYS)
    write_event("keys", action="add", kid=k.kid)
    return {"ok": True}

@app.delete("/auth/keys/{kid}")
def del_key(kid: str):
    PUBKEYS.pop(kid, None)
    _save_json(PUBKEYS_PATH, PUBKEYS)
    write_event("keys", action="del", kid=kid)
    return {"ok": True}

# ===== Auth (JWS diag) =====
@app.get("/auth/challenge")
def get_challenge(aud: str = Query("diag")):
    ch = new_nonce(aud)
    return ch.dict()

@app.post("/api/diag/echo")
def diag_echo(request: Request):
    return {
        "ok": True,
        "ip": request.client.host if request.client else "?",
        "headers": {"user-agent": (request.headers.get("user-agent") or "")[:120]},
        "ts": int(time.time()),
    }

@app.post("/api/diag/echo_signed")
def diag_echo_signed(req: SignedJWS, request: Request):
    def bad(code: str):
        return JSONResponse({"ok": False, "err": code}, status_code=400)
    try:
        parts = (req.jws or "").split(".")
        if len(parts) != 3:
            return bad("bad-jws")
        h, p, s = parts
        hdr = json.loads(b64u_decode(h))
        kid = hdr.get("kid")
        if not kid or kid not in PUBKEYS:
            return bad("unknown-kid")
        sig = b64u_decode(s)
        msg = (h + "." + p).encode()
        pk = b64u_decode(PUBKEYS[kid])
        VerifyKey(pk).verify(msg, sig)
        payload = json.loads(b64u_decode(p))
        now = int(time.time())
        nonce = payload.get("nonce")
        if not nonce or nonce not in NONCES:
            return bad("nonce-expired")
        if payload.get("aud") != "diag":
            return bad("bad-aud")
        nonce = payload.get("nonce")
        exp = NONCES.get(nonce)
        if not exp or exp < now:
            return bad("nonce-expired")
        NONCES.pop(nonce, None)
    except Exception:
        return bad("bad-payload")
    base = diag_echo(request)
    base["verified"] = True
    base["kid"] = kid
    base["payload"] = payload
    return base

# ===== Prometheus exporter =====
@app.get("/metrics")
def metrics():
    now = time.time()
    # Lightweight cache to avoid recomputing on hot scrapes
    if (now - METRICS_CACHE["ts"]) < METRICS_TTL and METRICS_CACHE["body"]:
        return PlainTextResponse(METRICS_CACHE["body"], media_type="text/plain; version=0.0.4")

    summ = _compute_summary(1000)
    health = _compute_health(300)
    lines: List[str] = []

    def h(x: str):
        lines.append(x)

    h("# HELP mecloneme_uptime_seconds Service uptime in seconds")
    h("# TYPE mecloneme_uptime_seconds gauge")
    h(f"mecloneme_uptime_seconds {int(time.time())-BOOT_TS}")
    h("# HELP mecloneme_requests_total Total observed HTTP requests (process lifetime)")
    h("# TYPE mecloneme_requests_total counter")
    h(f"mecloneme_requests_total {REQ_COUNT}")
    h("# HELP mecloneme_sessions Current active sessions")
    h("# TYPE mecloneme_sessions gauge")
    h(f"mecloneme_sessions {len(SESSIONS)}")
    h("# HELP mecloneme_pubkeys Registered public keys")
    h("# TYPE mecloneme_pubkeys gauge")
    h(f"mecloneme_pubkeys {len(PUBKEYS)}")
    h("# HELP mecloneme_latency_p95_ms Recent p95 latency (ms)")
    h("# TYPE mecloneme_latency_p95_ms gauge")
    h(f"mecloneme_latency_p95_ms {health['latency_ms']['p95']}")
    h("# HELP mecloneme_http_status_recent_total Recent sample status distribution")
    h("# TYPE mecloneme_http_status_recent_total gauge")
    for code, count in (summ["statuses"] or {}).items():
        h(f'mecloneme_http_status_recent_total{{code="{code}"}} {count}')
    body = "\n".join(lines) + "\n"
    METRICS_CACHE["ts"] = now
    METRICS_CACHE["body"] = body
    return PlainTextResponse(body, media_type="text/plain; version=0.0.4")

# ===== AR engine (stub) =====
@app.get("/ar/ping")
def ar_ping():
    return {"ok": True, "engine": "stub", "ts": int(time.time())}

@app.get("/ar/stub/avatar.svg", response_class=PlainTextResponse)
def ar_stub_avatar(mood: str = Query("neutral"), scale: int = Query(64, ge=32, le=256)):
    """Ultra-lekki SVG awatara (oczy + łuk ust). Zero bibliotek."""
    size = scale
    eye_dx = size*0.22
    eye_y = size*0.38
    mouth_y = size*0.68
    # Mouth curvature by mood
    k = {"happy": -0.18, "neutral": 0.0, "sad": 0.18}.get(mood, 0.0)
    mouth = f"M {size*0.25} {mouth_y} Q {size*0.5} {mouth_y + size*k} {size*0.75} {mouth_y}"
    svg = f"""
<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {size} {size}">
  <circle cx="{size/2 - eye_dx}" cy="{eye_y}" r="{size*0.06}" fill="#111"/>
  <circle cx="{size/2 + eye_dx}" cy="{eye_y}" r="{size*0.06}" fill="#111"/>
  <path d="{mouth}" stroke="#111" stroke-width="{size*0.04}" fill="none" stroke-linecap="round"/>
</svg>"""
    return PlainTextResponse(svg, media_type="image/svg+xml")

@app.get("/ar/stub/state")
def ar_stub_state():
    return {"ok": True, "mood": "neutral", "ts": int(time.time())}

# ===== Export / Telemetry =====
class ExportWebhook(BaseModel):
    url: str
    payload: Optional[Dict[str, Any]] = None
    headers: Optional[Dict[str, str]] = None

def _http_post_json(url: str, payload: Dict[str, Any], headers: Optional[Dict[str, str]] = None, timeout_s: int = 3) -> Dict[str, Any]:
    import urllib.request, json as _json
    data = _json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        body = resp.read().decode("utf-8", "ignore")
        return {"status": resp.status, "body": body}

@app.post("/export/webhook")

def export_webhook(inp: ExportWebhook):
    """Forward JSON payload to an external webhook with tiny retry logic."""
    payload = inp.payload or {"ok": True, "src": "mecloneme"}
    tries = [0.0, 0.5, 1.0]
    last = {"status": 0, "body": ""}
    for backoff in tries:
        try:
            last = _http_post_json(inp.url, payload, headers=inp.headers)
            if 200 <= last["status"] < 300:
                break
        except Exception as e:
            last = {"status": 0, "body": f"err: {e}"}
        time.sleep(backoff)
    return {"ok": 200 <= (last.get("status") or 0) < 300, "last": last}

class ExportS3Like(BaseModel):
    url: str
    content_b64: Optional[str] = None
    text: Optional[str] = None
    content_type: Optional[str] = "application/octet-stream"
    method: Optional[str] = "PUT"

@app.post("/export/s3")

def export_s3_like(inp: ExportS3Like):
    """Upload to a pre-signed URL (S3-compatible). No external deps."""
    import urllib.request
    if not (inp.content_b64 or inp.text):
        return JSONResponse({"ok": False, "err": "no-content"}, status_code=400)
    data = base64.b64decode(inp.content_b64) if inp.content_b64 else inp.text.encode()
    req = urllib.request.Request(inp.url, data=data, method=inp.method or "PUT")
    if inp.content_type:
        req.add_header("Content-Type", inp.content_type)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return {"ok": 200 <= resp.status < 400, "status": resp.status}
    except Exception as e:
        return JSONResponse({"ok": False, "err": str(e)}, status_code=502)

# ===== Ingest (przywrócony, lekki) =====
class IngestPayload(BaseModel):
    tag: Optional[str] = "default"
    data: Dict[str, Any]

@app.post("/ingest")

def ingest(inp: IngestPayload):
    write_shadow(kind="ingest", tag=inp.tag, data=inp.data)
    write_event("ingest", tag=inp.tag)
    return {"ok": True}

# ===== HTML (panel + mobile) =====
PANEL_HTML = """<!doctype html>
<meta charset="utf-8"><title>Guardian — mini panel</title>
<style>
  :root{ --ok:#2e7d32; --warn:#e09100; --crit:#c62828; --btn:#f3f3f3; --bd:#e5e5e5; }
  body{font-family: system-ui,-apple-system,Segoe UI,Roboto;margin:16px}
  .card{border:1px solid var(--bd);border-radius:8px;padding:12px}
  .btn{padding:6px 10px;border:1px solid var(--bd);border-radius:8px;background:var(--btn);cursor:pointer}
  .btn[disabled]{opacity:.5}
  .row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
  .kv{font:12px/1.1 monospace}
  .meter{height:6px;background:#eee;border-radius:8px;position:relative}
  .meter>i{position:absolute;left:0;top:0;bottom:0;background:var(--ok);border-radius:8px}
  .ok{color:var(--ok)} .warn{color:var(--warn)} .crit{color:var(--crit)}
  #heatmap,#heatmapS{display:grid;grid-template-columns: repeat(10, 1fr);gap:4px}
  .cell{height:22px;background:#eee;border-radius:3px;text-align:center;font:11px/22px system-ui}
</style>
<div class=card>
  <div class=row>
    <button id=ping class=btn>Ping API</button>
    <button id=getDiag class=btn>Diag challenge</button>
    <button id=echoSigned class=btn>Echo (signed)</button>
    <button id=logout class=btn>Logout</button>
  </div>
  <pre id=out class=kv></pre>
</div>
<div class=card>
  <div class=row>
    <button id=metrics class=btn>Metrics</button>
    <button id=health class=btn>Health</button>
    <button id=spark class=btn>Series</button>
  </div>
  <pre id=metOut class=kv></pre>
  <canvas id=sparkCanvas width=360 height=80 style="width:360px;height:80px;border:1px solid #eee;border-radius:6px"></canvas>
  <div id=heatmap style="margin-top:8px"></div>
</div>
<div class=card>
  <div class=row>
    <input id=kid placeholder="kid" value="dev-key-1" class=btn>
    <button id=arPing class=btn>AR /ping</button>
    <a href="/ar/stub/avatar.svg?mood=happy" class=btn target=_blank>AR avatar (SVG)</a>
  </div>
  <pre id=arOut class=kv></pre>
</div>
<script>
const $=id=>document.getElementById(id);
const enc = new TextEncoder();

$("ping").onclick=async()=>{
  const r=await fetch("/api/health");
  $("out").textContent=JSON.stringify(await r.json(),null,2);
};

$("metrics").onclick=async()=>{
  const r=await fetch("/metrics");
  $("metOut").textContent=await r.text();
};

$("health").onclick=async()=>{
  const r=await fetch("/api/health");
  $("metOut").textContent=JSON.stringify(await r.json(),null,2);
};

$("spark").onclick=async()=>{
  const r=await fetch("/api/series");
  const j=await r.json();
  const cv=$("sparkCanvas"); const ctx=cv.getContext("2d");
  ctx.clearRect(0,0,cv.width,cv.height);
  const s=j.series||[]; if(!s.length) return;
  const pad=6,w=cv.width-pad*2,h=cv.height-pad*2;
  const maxC=Math.max(...s.map(x=>x.count));
  ctx.beginPath();
  s.forEach((x,i)=>{const y=h*(1-(x.count/(maxC||1)));const X=pad+i*(w/(s.length-1)); if(i)ctx.lineTo(X,y); else ctx.moveTo(X,y);});
  ctx.lineWidth=2; ctx.strokeStyle="#2e7d32"; ctx.stroke();
};

$("arPing").onclick=async()=>{
  const r=await fetch("/ar/ping");
  $("arOut").textContent=JSON.stringify(await r.json(),null,2);
};
</script>
"""

MOBILE_HTML = """<!doctype html>
<meta charset="utf-8"><title>Mobile</title>
<style>body{font-family:system-ui;margin:16px}</style>
<h3>Mobile stub</h3>
<p>TODO</p>
"""

# ===== Series endpoint =====
@app.get("/api/series")
def series(minutes: int = Query(30, ge=1, le=240)):
    return _compute_series(minutes)

# ===== Alerts =====
async def emit_alert(level: str, msg: str, cooldown_s: int = 5, **extra):
    now = int(time.time())
    key = f"{level}:{msg}"
    last = LAST_ALERT_TS.get(key, 0)
    if now - last < cooldown_s:
        return
    LAST_ALERT_TS[key] = now
    entry = Alert(level=level, msg=msg, ts=now, extra=extra).dict()
    ALERTS.append(entry)
    ALERTS[:] = ALERTS[-200:]
    write_shadow(kind="alert", **entry)

@app.get("/alerts")
def list_alerts():
    return {"ok": True, "alerts": ALERTS[-50:]}

# ===== Snapshots =====
@app.post("/snapshots")
def save_snapshot(s: Snapshot):
    name = re.sub(r"[^a-zA-Z0-9_-]", "_", s.tag)[:48]
    path = os.path.join(SNAPS_DIR, f"{name}_{int(time.time())}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"ts": int(time.time()), "payload": s.payload}, f)
    return {"ok": True, "path": path}

# ===== Admin =====
@app.post("/admin/clear")
def admin_clear():
    for p in [EVENTS_JSONL, SHADOW_JSONL]:
        try:
            open(p, "w").close()
        except Exception:
            pass
    return {"ok": True}
