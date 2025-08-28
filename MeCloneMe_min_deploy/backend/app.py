# === N09-FIXID-20250828T125631Z ===
import os, time, json, base64, secrets, re
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from nacl.signing import VerifyKey
from backend.n09_coalescer import router as alerts_router

# ===== Config loader (MC_CONFIG / MC_* / legacy) =====
def _parse_mc_config() -> Dict[str, Any]:
    raw = os.getenv("MC_CONFIG", "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        cfg: Dict[str, Any] = {}
        for item in raw.split(";"):
            if "=" in item:
                k, v = item.split("=", 1)
                cfg[k.strip()] = v.strip()
        return cfg

_MC = _parse_mc_config()

def _pick_str(json_key: str, env_aliases: List[str], default: str) -> str:
    if json_key in _MC:
        return str(_MC[json_key])
    for k in env_aliases:
        v = os.getenv(k)
        if v not in (None, ""):
            return str(v)
    return default

def _pick_int(json_key: str, env_aliases: List[str], default: int) -> int:
    if json_key in _MC:
        try:
            return int(_MC[json_key])
        except Exception:
            pass
    for k in env_aliases:
        v = os.getenv(k)
        if v not in (None, ""):
            try:
                return int(v)
            except Exception:
                pass
    return default

def _pick_float(json_key: str, env_aliases: List[str], default: float) -> float:
    if json_key in _MC:
        try:
            return float(_MC[json_key])
        except Exception:
            pass
    for k in env_aliases:
        v = os.getenv(k)
        if v not in (None, ""):
            try:
                return float(v)
            except Exception:
                pass
    return default

def _pick_csv(json_key: str, env_aliases: List[str], default: str) -> str:
    if json_key in _MC:
        return str(_MC[json_key])
    for k in env_aliases:
        v = os.getenv(k)
        if v not in (None, ""):
            return str(v)
    return default

# ===== Helpers =====
def b64u_decode(s: str) -> bytes:
    s = s or ""
    s += "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s.encode())

# ===== Config =====
API_VERSION = _pick_str("api_version", ["MC_API_VERSION","API_VERSION"], "0.3.10")
THEME       = _pick_str("theme",       ["MC_THEME","THEME"], "dark")  # dark | light

NONCE_TTL   = _pick_int("nonce_ttl",   ["MC_NONCE_TTL","NONCE_TTL"], 300)
SESSION_TTL = _pick_int("session_ttl", ["MC_SESSION_TTL","SESSION_TTL"], 3600)

RATE_WINDOW = _pick_int("rate_window", ["MC_RATE_WINDOW","RATE_WINDOW"], 60)
RATE_MAX    = _pick_int("rate_max",    ["MC_RATE_MAX","RATE_MAX"], 120)

P95_WARN    = _pick_int("p95_warn",    ["MC_P95_WARN","P95_WARN"], 300)
P95_CRIT    = _pick_int("p95_crit",    ["MC_P95_CRIT","P95_CRIT"], 800)

METRICS_TTL = _pick_float("metrics_ttl", ["MC_METRICS_TTL","METRICS_TTL"], 2.0)
AUDIT_SAMPLE_N = _pick_int("audit_sample_n", ["MC_AUDIT_SAMPLE_N","AUDIT_SAMPLE_N"], 1)
SKIP_AUDIT_PATHS = set(_pick_csv("skip_audit_paths", ["MC_SKIP_AUDIT_PATHS","SKIP_AUDIT_PATHS"], "/metrics,/health,/ar/ping").split(","))

# ===== App =====
app = FastAPI(title="MeCloneMe Mini API", version=API_VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

# ===== Stores =====
DATA_DIR = "data"; LOG_DIR = "logs"; SNAPS_DIR = os.path.join(LOG_DIR, "snaps")
os.makedirs(DATA_DIR, exist_ok=True); os.makedirs(LOG_DIR, exist_ok=True); os.makedirs(SNAPS_DIR, exist_ok=True)

PUBKEYS: Dict[str, str] = {}
SESSIONS: Dict[str, Dict[str, Any]] = {}
RATE: Dict[str, List[int]] = {}
ALERTS: List[Dict[str, Any]] = []
LAST_ALERT_TS: Dict[str, int] = {}

PUBKEYS_PATH = os.path.join(DATA_DIR, "pubkeys.json")
SESSIONS_PATH = os.path.join(DATA_DIR, "sessions.json")
EVENTS_JSONL = os.path.join(LOG_DIR, "events.jsonl")
SHADOW_JSONL = os.path.join(LOG_DIR, "shadow.jsonl")
PROGRESS_PATH = os.path.join(DATA_DIR, "progress.json")

BOOT_TS = int(time.time())
REQ_COUNT = 0
METRICS_CACHE = {"ts": 0.0, "body": ""}

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
    fields["kind"] = kind; fields["ts"] = int(time.time())
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

# ===== Percentyle/metryki =====
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

def _compute_summary(n: int) -> Dict[str, Any]:
    rows = [r for r in tail_jsonl(EVENTS_JSONL, n) if r.get("kind") == "http"]
    by_status: Dict[str, int] = {}
    by_path: Dict[str, int] = {}
    by_method: Dict[str, int] = {}
    lat: List[int] = []
    for r in rows:
        by_status[str(r.get("status", "?"))] = by_status.get(str(r.get("status", "?")), 0) + 1
        by_path[r.get("path", "?")] = by_path.get(r.get("path", "?"), 0) + 1
        by_method[r.get("method", "?")] = by_method.get(r.get("method", "?"), 0) + 1
        if isinstance(r.get("ms"), int):
            lat.append(r["ms"])
    return {
        "statuses": by_status,
        "paths_top5": sorted(by_path.items(), key=lambda x: x[1], reverse=True)[:5],
        "methods": by_method,
        "latency_ms": _lat_stats(lat),
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

# ===== Theming =====
def _theme_vars(theme: str) -> Dict[str, str]:
    if theme == "light":
        return {
            "bg":"#ffffff","tx":"#111","bd":"#e5e5e5","card":"#fafafa",
            "ok":"#2e7d32","warn":"#e09100","crit":"#c62828","btn":"#f3f3f3"
        }
    return {
        "bg":"#0b0e11","tx":"#eaeef3","bd":"#1a1f24","card":"#0f1318",
        "ok":"#4ade80","warn":"#fbbf24","crit":"#f87171","btn":"#0f1720"
    }

# ===== UI (paski postępu; bez listy i bez Gantta) =====
def render_panel_html() -> str:
    v = _theme_vars(THEME)
    css = (":root{"
           f" --bg:{v['bg']}; --tx:{v['tx']}; --bd:{v['bd']}; --card:{v['card']}; --btn:{v['btn']};"
           f" --ok:{v['ok']}; --warn:{v['warn']}; --crit:{v['crit']}; }}")
    tpl = '''<!doctype html>
<meta charset="utf-8"><title>MeCloneMe — Panel (mini)</title>
<style>
  __CSS__
  html,body { background:var(--bg); color:var(--tx); height:100%; margin:0; font-family:system-ui,-apple-system,Segoe UI,Roboto; }
  .wrap { padding:16px; max-width:980px; margin:0 auto; }
  .card { border:1px solid var(--bd); border-radius:10px; padding:12px; background:var(--card); margin:10px 0; }
  .btn { padding:8px 12px; border:1px solid var(--bd); border-radius:8px; background:var(--btn); color:var(--tx); cursor:pointer; }
  .row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
  .kv { font:12px/1.2 ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas; }
  .meter { height:10px; background:var(--bd); border-radius:999px; position:relative; overflow:hidden; }
  .meter>i { position:absolute; left:0; top:0; bottom:0; background:linear-gradient(90deg,var(--ok),#22d3ee); border-radius:999px; }
  .grid { display:grid; grid-template-columns:260px 1fr; gap:10px; }
  @media (max-width:820px) { .grid { grid-template-columns:1fr; } }
  .legend { font-size:12px; opacity:.8; }
</style>
<div class="wrap">
  <div class="card">
    <div class="row">
      <button id="ping" class="btn">Ping API</button>
      <button id="diag" class="btn">Diag challenge</button>
      <a href="/ar/stub/avatar.svg?mood=happy" class="btn" target="_blank">AR avatar (SVG)</a>
      <button id="refreshProgress" class="btn">Odśwież postęp</button>
      <button id="refreshBars" class="btn">Odśwież paski</button>
    </div>
    <small>v__API_VERSION__ • motyw: __THEME__</small>
  </div>

  <div class="grid">
    <div class="card">
      <div class="row"><button id="metrics" class="btn">Metrics</button><button id="health" class="btn">Health</button><button id="spark" class="btn">Series</button></div>
      <pre id="metOut" class="kv"></pre>
      <canvas id="sparkCanvas" width="420" height="90" style="width:100%;max-width:420px;height:90px;border:1px solid var(--bd);border-radius:8px"></canvas>
    </div>

    <div class="card">
      <h3 style="margin:6px 0">Postęp MeCloneMe</h3>
      <div class="meter"><i id="overall" style="width:0%"></i></div>
      <div class="legend" style="margin-top:6px">Zielony = zrealizowane, jaśniejszy szary = pozostały zakres</div>
      <div id="barsBox" style="margin-top:10px;border:1px solid var(--bd);border-radius:8px;overflow:hidden"></div>
    </div>
  </div>

  <div class="card"><pre id="out" class="kv"></pre></div>
</div>
<script>
const $=id=>document.getElementById(id);
$("ping").onclick=async()=>{ const r=await fetch("/api/health"); $("out").textContent=JSON.stringify(await r.json(),null,2); };
$("metrics").onclick=async()=>{ const r=await fetch("/metrics"); $("metOut").textContent=await r.text(); };
$("health").onclick=async()=>{ const r=await fetch("/api/health"); $("metOut").textContent=JSON.stringify(await r.json(),null,2); };
$("spark").onclick=async()=>{ const r=await fetch("/api/series"); const j=await r.json(); const cv=$("sparkCanvas"),ctx=cv.getContext("2d"); ctx.clearRect(0,0,cv.width,cv.height); const s=j.series||[]; if(!s.length) return; const pad=6,w=cv.width-pad*2,h=cv.height-pad*2; const maxC=Math.max(...s.map(x=>x.count)); ctx.beginPath(); s.forEach((x,i)=>{const y=h*(1-(x.count/(maxC||1))); const X=pad+i*(w/(s.length-1)); if(i)ctx.lineTo(X,y); else ctx.moveTo(X,y);}); ctx.lineWidth=2; ctx.strokeStyle=getComputedStyle(document.documentElement).getPropertyValue('--ok'); ctx.stroke(); };

async function loadProgress(){ const r=await fetch('/progress'); const j=await r.json(); $("overall").style.width=(j.overall||0)+"%"; }
async function loadBars(){ try{ const r=await fetch('/progress/bars.svg?ts='+Date.now()); const svg=await r.text(); $("barsBox").innerHTML = svg; } catch(e) { $("barsBox").innerHTML='<div style="padding:8px">Nie udało się załadować pasków.</div>'; } }

$("refreshProgress").onclick=loadProgress;
$("refreshBars").onclick=loadBars;
loadProgress(); loadBars();
</script>
'''
    return tpl.replace("__API_VERSION__", API_VERSION).replace("__THEME__", THEME).replace("__CSS__", css)

def render_mobile_html() -> str:
    v = _theme_vars(THEME)
    css = (":root{"
           f" --bg:{v['bg']}; --tx:{v['tx']}; --bd:{v['bd']}; --ok:{v['ok']}; --warn:{v['warn']}; --crit:{v['crit']}; }}")
    tpl = '''<!doctype html>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Mobile stub</title>
<style>
  __CSS__
  body { background:var(--bg); color:var(--tx); font-family:system-ui; margin:12px; }
  .card { border:1px solid var(--bd); border-radius:12px; padding:12px; background:rgba(255,255,255,.02); }
  .meter { height:10px; background:var(--bd); border-radius:999px; position:relative; overflow:hidden; }
  .meter>i { position:absolute; left:0; top:0; bottom:0; background:linear-gradient(90deg,var(--ok),#22d3ee); border-radius:999px; }
  .legend { font-size:12px; opacity:.8; }
</style>
<h2>Mobile (dark)</h2>
<div class="card">
  <div>Postęp MeCloneMe</div>
  <div class="meter"><i id="ov" style="width:0%"></i></div>
  <div class="legend" style="margin-top:6px">Zielony = zrealizowane, jaśniejszy szary = pozostały zakres</div>
  <div id="barsM" style="width:100%;margin-top:8px;border:1px solid var(--bd);border-radius:8px;overflow:hidden"></div>
</div>
<script>
(async()=>{ 
  const r=await fetch('/progress'); const j=await r.json(); 
  document.getElementById('ov').style.width=(j.overall||0)+'%'; 
  try{ const svg=await (await fetch('/progress/bars.svg?ts='+Date.now())).text(); document.getElementById('barsM').innerHTML=svg; }catch(e){ document.getElementById('barsM').innerHTML='(brak pasków)'; }
})();
</script>
'''
    return tpl.replace("__CSS__", css)

# ===== UI endpoints =====
@app.get("/", response_class=HTMLResponse)
def root(): return HTMLResponse(render_panel_html())

@app.get("/mobile", response_class=HTMLResponse)
def mobile_page(): return HTMLResponse(render_mobile_html())

# ===== Health =====
@app.get("/api/health")
def health(): return {"ok": True, "version": API_VERSION, "ts": int(time.time())}

# ===== Keys & auth diag =====
class PubKey(BaseModel): kid: str; key: str
@app.get("/auth/keys")
def list_keys(): return {"ok": True, "keys": [{"kid": k, "key": v} for k, v in PUBKEYS.items()]}

@app.post("/auth/keys")
def add_key(k: PubKey):
    PUBKEYS[k.kid]=k.key; _save_json(PUBKEYS_PATH, PUBKEYS); write_event("keys", action="add", kid=k.kid); return {"ok": True}

@app.delete("/auth/keys/{kid}")
def del_key(kid: str):
    PUBKEYS.pop(kid, None); _save_json(PUBKEYS_PATH, PUBKEYS); write_event("keys", action="del", kid=kid); return {"ok": True}

@app.get("/auth/challenge")
def get_challenge(aud: str = Query("diag")): return new_nonce(aud)

class SignedJWS(BaseModel): jws: str
@app.post("/api/diag/echo")
def diag_echo(request: Request):
    return {"ok": True, "ip": request.client.host if request.client else "?",
            "headers": {"user-agent": (request.headers.get("user-agent") or "")[:120]},
            "ts": int(time.time())}

@app.post("/api/diag/echo_signed")
def diag_echo_signed(req: SignedJWS, request: Request):
    def bad(code: str): return JSONResponse({"ok": False, "err": code}, status_code=400)
    try:
        parts=(req.jws or "").split("."); 
        if len(parts)!=3: return bad("bad-jws")
        h,p,s=parts; hdr=json.loads(b64u_decode(h)); kid=hdr.get("kid")
        if not kid or kid not in PUBKEYS: return bad("unknown-kid")
        sig=base64.urlsafe_b64decode(s+"="*(-len(s)%4)); msg=(h+"."+p).encode()
        pk=base64.urlsafe_b64decode(PUBKEYS[kid]+"="*(-len(PUBKEYS[kid])%4)); VerifyKey(pk).verify(msg,sig)
        payload=json.loads(b64u_decode(p)); now=int(time.time()); nonce=payload.get("nonce")
        if not nonce or nonce not in NONCES: return bad("nonce-expired")
        if payload.get("aud")!="diag": return bad("bad-aud")
        exp = NONCES.get(nonce)
        if (not exp) or (exp < now): return bad("nonce-expired")
        NONCES.pop(nonce,None)
    except Exception:
        return bad("bad-payload")
    base = diag_echo(request); base.update({"verified": True, "kid": kid, "payload": payload}); return base

# ===== Metrics =====
@app.get("/metrics")
def metrics():
    now=time.time()
    if (now - METRICS_CACHE["ts"]) < METRICS_TTL and METRICS_CACHE["body"]:
        return PlainTextResponse(METRICS_CACHE["body"], media_type="text/plain; version=0.0.4")
    summ=_compute_summary(1000); health=_compute_health(300)
    L: List[str]=[]
    def h(x:str): L.append(x)
    h("# HELP mecloneme_uptime_seconds Service uptime in seconds"); h("# TYPE mecloneme_uptime_seconds gauge"); h(f"mecloneme_uptime_seconds {int(time.time())-BOOT_TS}")
    h("# HELP mecloneme_requests_total Total observed HTTP requests (process lifetime)"); h("# TYPE mecloneme_requests_total counter"); h(f"mecloneme_requests_total {REQ_COUNT}")
    h("# HELP mecloneme_sessions Current active sessions"); h("# TYPE mecloneme_sessions gauge"); h(f"mecloneme_sessions {len(SESSIONS)}")
    h("# HELP mecloneme_pubkeys Registered public keys"); h("# TYPE mecloneme_pubkeys gauge"); h(f"mecloneme_pubkeys {len(PUBKEYS)}")
    h("# HELP mecloneme_latency_p95_ms Recent p95 latency (ms)"); h("# TYPE mecloneme_latency_p95_ms gauge"); h(f"mecloneme_latency_p95_ms {health['latency_ms']['p95']}")
    h("# HELP mecloneme_http_status_recent_total Recent sample status distribution"); h("# TYPE mecloneme_http_status_recent_total gauge")
    for code,count in (summ.get("statuses") or {}).items(): h(f'mecloneme_http_status_recent_total{{code="{code}"}} {count}')
    body="\n".join(L)+"\n"; METRICS_CACHE.update(ts=now, body=body)
    return PlainTextResponse(body, media_type="text/plain; version=0.0.4")

# ===== Series =====
@app.get("/api/series")
def series(minutes: int = Query(30, ge=1, le=240)): return _compute_series(minutes)

# ===== AR stub =====
@app.get("/ar/ping")
def ar_ping(): return {"ok": True, "engine": "stub", "ts": int(time.time())}

@app.get("/ar/stub/avatar.svg", response_class=PlainTextResponse)
def ar_stub_avatar(mood: str = Query("neutral"), scale: int = Query(64, ge=32, le=256)):
    size=scale; eye_dx=size*0.22; eye_y=size*0.38; mouth_y=size*0.68
    k={"happy":-0.18,"neutral":0.0,"sad":0.18}.get(mood,0.0); mouth=f"M {size*0.25} {mouth_y} Q {size*0.5} {mouth_y + size*k} {size*0.75} {mouth_y}"
    svg=f"""
<svg xmlns='http://www.w3.org/2000/svg' width='{size}' height='{size}' viewBox='0 0 {size} {size}'>
  <circle cx='{size/2 - eye_dx}' cy='{eye_y}' r='{size*0.06}' fill='#111'/>
  <circle cx='{size/2 + eye_dx}' cy='{eye_y}' r='{size*0.06}' fill='#111'/>
  <path d='{mouth}' stroke='#111' stroke-width='{size*0.04}' fill='none' stroke-linecap='round'/>
</svg>"""
    return PlainTextResponse(svg, media_type="image/svg+xml")

@app.get("/ar/stub/state")
def ar_stub_state(): return {"ok": True, "mood": "neutral", "ts": int(time.time())}

# ===== Export / Telemetria =====
class ExportWebhook(BaseModel):
    url: str; payload: Optional[Dict[str, Any]] = None; headers: Optional[Dict[str, str]] = None

def _http_post_json(url: str, payload: Dict[str, Any], headers: Optional[Dict[str, str]] = None, timeout_s: int = 3) -> Dict[str, Any]:
    import urllib.request, json as _json
    data=_json.dumps(payload).encode()
    req=urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type","application/json")
    if headers:
        for k,v in headers.items(): req.add_header(k,v)
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        body=resp.read().decode("utf-8","ignore")
        return {"status": resp.status, "body": body}

@app.post("/export/webhook")
def export_webhook(inp: ExportWebhook):
    payload=inp.payload or {"ok": True, "src":"mecloneme"}; tries=[0.0,0.5,1.0]; last={"status":0,"body":""}
    for backoff in tries:
        try:
            last=_http_post_json(inp.url, payload, headers=inp.headers)
            if 200 <= last["status"] < 300: break
        except Exception as e: last={"status":0,"body":f"err: {e}"}
        time.sleep(backoff)
    return {"ok": 200 <= (last.get("status") or 0) < 300, "last": last}

class ExportS3Like(BaseModel):
    url: str; content_b64: Optional[str]=None; text: Optional[str]=None; content_type: Optional[str]="application/octet-stream"; method: Optional[str]="PUT"
@app.post("/export/s3")
def export_s3_like(inp: ExportS3Like):
    import urllib.request
    if not (inp.content_b64 or inp.text): return JSONResponse({"ok": False, "err":"no-content"}, status_code=400)
    data=base64.b64decode(inp.content_b64) if inp.content_b64 else inp.text.encode()
    req=urllib.request.Request(inp.url, data=data, method=inp.method or "PUT")
    if inp.content_type: req.add_header("Content-Type", inp.content_type)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp: return {"ok": 200 <= resp.status < 400, "status": resp.status}
    except Exception as e: return JSONResponse({"ok": False, "err": str(e)}, status_code=502)

# ===== Progress (paski) =====
PROGRESS_DEFAULT: Dict[str, int] = {
    "N01 — SSOT / Router-README": 55,
    "N18 — Panel CEO": 35,
    "N22 — Testy & QA": 25,
    "N04 — Mobile (Camera/Mic)": 20,
    "N05 — Desktop (Bridge)": 20,
    "N09 — Guardian": 30,
    "N21 — SDK / API Clients": 15,
    "N27 — Docs & OpenAPI": 30,
    "N30 — Core (Live+AR+Guardian)": 40,
}
def _load_progress() -> Dict[str, int]:
    obj=_load_json(PROGRESS_PATH, {})
    if not obj: obj=PROGRESS_DEFAULT.copy(); _save_json(PROGRESS_PATH, obj)
    return obj
def _save_progress(obj: Dict[str, int]): _save_json(PROGRESS_PATH, obj)
def _progress_overall(mods: Dict[str, int]) -> int:
    if not mods: return 0
    vals=[max(0, min(100, int(v))) for v in mods.values()]
    return int(sum(vals)/len(vals))

@app.get("/progress")
def get_progress():
    mods=_load_progress(); return {"ok": True, "overall": _progress_overall(mods), "modules": mods}

@app.get("/progress/bars.svg", response_class=PlainTextResponse)
def bars_svg():
    """
    Proste paski postępu:
      • stała kolumna tytułów po lewej (wyściełana tak, by nie nachodziły napisy)
      • jasnoszary pełny pasek planu
      • zielone wypełnienie = % realizacji
      • etykieta XX% przy prawej krawędzi zielonego, czarnym kolorem
    """
    mods=_load_progress()
    # układ
    width=900
    title_col=270  # odsunięcie pasków w prawo (dopasowane do najdłuższego "N01 — ...")
    row_h=28
    pad_y=16
    inner_pad_x=10
    avail = width - title_col - 20
    height = pad_y*2 + row_h*len(mods)

    v=_theme_vars(THEME)
    text=v['tx']
    plan="#9aa3af"   # jaśniejszy, dobrze widoczny szary (na dark tle)
    done=v['ok']
    label="#000000"  # czarna etykieta %

    parts=[f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' viewBox='0 0 {width} {height}'>",
           f"<rect x='0' y='0' width='{width}' height='{height}' fill='transparent'/>",
           f"<g fill='{text}' font-size='13'>"]
    y=pad_y
    for name, percent in mods.items():
        # tytuł po lewej
        parts.append(f"<text x='10' y='{y+row_h-10}'>{name}</text>")
        # tło planu (jasnoszary)
        parts.append(f"<rect x='{title_col:.1f}' y='{y+6}' rx='7' ry='7' width='{avail:.1f}' height='{row_h-12}' fill='{plan}' opacity='0.85' />")
        # wypełnienie (zielone)
        p=max(0, min(100, int(percent)))
        fill_w = max(0.0, avail * (p/100.0))
        if fill_w > 0:
            parts.append(f"<rect x='{title_col:.1f}' y='{y+6}' rx='7' ry='7' width='{fill_w:.1f}' height='{row_h-12}' fill='{done}' opacity='0.98' />")
            # etykieta % (czarna)
            tx = title_col + fill_w - 6 if fill_w >= 30 else title_col + fill_w + 10
            anchor = "end" if fill_w >= 30 else "start"
            parts.append(f"<text x='{tx:.1f}' y='{y+row_h-10}' text-anchor='{anchor}' font-size='12' fill='{label}'>{p}%</text>")
        y += row_h
    parts.append("</g></svg>")
    return PlainTextResponse("".join(parts), media_type="image/svg+xml")

@app.post("/admin/progress")
def set_progress(payload: Dict[str, Any]):
    mods=_load_progress()
    if "modules" in payload and isinstance(payload["modules"], dict):
        for k,v in payload["modules"].items():
            try: mods[k]=int(v)
            except Exception: pass
    if "module" in payload and "percent" in payload:
        try: mods[payload["module"]]=int(payload["percent"])
        except Exception: pass
    _save_progress(mods); return {"ok": True, "overall": _progress_overall(mods), "modules": mods}

# ===== Alerts (opcjonalnie podgląd) =====
class Alert(BaseModel): level: str; msg: str; ts: int; extra: Optional[Dict[str, Any]] = None
@app.get("/alerts")
def list_alerts(): 
    try: return {"ok": True, "alerts": tail_jsonl(SHADOW_JSONL, 200)}
    except Exception: return {"ok": True, "alerts": []}

# ===== Nonces & Sessions / WS =====
NONCES: Dict[str, int] = {}
def new_nonce(aud: str):
    nonce = base64.urlsafe_b64encode(secrets.token_bytes(24)).decode().rstrip("=")
    ts = int(time.time()); NONCES[nonce] = ts + NONCE_TTL
    return {"aud": aud, "nonce": nonce, "ts": ts}
def new_session(kid: str):
    sid = base64.urlsafe_b64encode(secrets.token_bytes(18)).decode().rstrip("=")
    exp = int(time.time()) + SESSION_TTL
    SESSIONS[sid] = {"kid": kid, "exp": exp}
    _save_json(SESSIONS_PATH, SESSIONS)
    return {"kid": kid, "sid": sid, "exp": exp}
def rate_is_hot(ip: str) -> Optional[int]:
    cnt = len(RATE.get(ip, [])); return cnt if cnt >= int(RATE_MAX * 0.8) else None

@app.websocket("/ws/echo")
async def ws_echo(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            msg = await ws.receive_text()
            await ws.send_text(msg)
    except WebSocketDisconnect:
        pass

# ===== Middleware audit =====
@app.middleware("http")
async def audit_mw(request: Request, call_next):
    global REQ_COUNT
    t0=time.perf_counter()
    ip=request.client.host if request.client else "?"
    path=request.url.path; method=request.method
    status=500
    try:
        response=await call_next(request)
        status=getattr(response,"status_code",200)
        return response
    finally:
        ms=int((time.perf_counter()-t0)*1000)
        REQ_COUNT+=1; reqno=REQ_COUNT
        if path in SKIP_AUDIT_PATHS: return
        if AUDIT_SAMPLE_N>1 and (reqno % AUDIT_SAMPLE_N)!=0: return
        write_event("http", ip=ip, method=method, path=path, status=status, ms=ms)

# ===== Startup =====
load_stores()