import os, time, json, base64, secrets, io, statistics, re
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
API_VERSION = os.getenv("API_VERSION","0.3.0")
NONCE_TTL   = int(os.getenv("NONCE_TTL", "300"))
SESSION_TTL = int(os.getenv("SESSION_TTL", "900"))
RATE_MAX    = int(os.getenv("RATE_MAX", "30"))
RATE_WINDOW = int(os.getenv("RATE_WINDOW", "10"))
P95_WARN    = int(os.getenv("P95_WARN", "300"))
P95_CRIT    = int(os.getenv("P95_CRIT", "800"))

# ===== In-memory stores (demo) =====
NONCES: Dict[str, int] = {}                 # nonce -> expiry ts
PUBKEYS: Dict[str, str] = {}                # kid -> public key (base64url 32B)
SESSIONS: Dict[str, Dict[str, Any]] = {}    # sid -> {kid, exp}
RATE: Dict[str, List[int]] = {}             # ip -> [timestamps]
ALERTS: List[Dict[str, Any]] = []           # rolling alerts

# ===== Simple persistence =====
DATA_DIR = "data"; LOG_DIR = "logs"; SNAPS_DIR = os.path.join(LOG_DIR,"snaps")
os.makedirs(DATA_DIR, exist_ok=True); os.makedirs(LOG_DIR, exist_ok=True); os.makedirs(SNAPS_DIR, exist_ok=True)
PUBKEYS_PATH = os.path.join(DATA_DIR, "pubkeys.json"); SESSIONS_PATH = os.path.join(DATA_DIR, "sessions.json")
EVENTS_JSONL = os.path.join(LOG_DIR,  "events.jsonl"); SHADOW_JSONL = os.path.join(LOG_DIR,  "shadow.jsonl")

BOOT_TS = int(time.time()); REQ_COUNT = 0
LAST_ALERT_TS: Dict[str,int] = {}   # throttling

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

def save_pubkeys(): _save_json(PUBKEYS_PATH, PUBKEYS)
def save_sessions(): _save_json(SESSIONS_PATH, SESSIONS)

# ===== Events (JSONL) =====
def write_event(kind: str, **data) -> None:
    # pozwól nadpisać ts gdy przychodzi z zewnątrz (ingest)
    ts_override = data.pop("ts", None)
    rec = {"ts": ts_override if isinstance(ts_override, int) else int(time.time()),
           "kind": kind, **data}
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

# ===== Alerts =====
def _throttle(key: str, cooldown_s: int) -> bool:
    now = int(time.time())
    last = LAST_ALERT_TS.get(key, 0)
    if now - last < cooldown_s:
        return False
    LAST_ALERT_TS[key] = now
    return True

async def emit(kind: str, **vec):
    frame = {"ts": int(time.time()), "vec": {kind: vec}}
    await ws_manager.broadcast(frame)

async def emit_alert(level: str, title: str, cooldown_s: int = 20, **meta):
    key = f"{level}:{title}"
    if not _throttle(key, cooldown_s):  # nie spamuj
        return
    rec = {"ts": int(time.time()), "level": level, "title": title, "meta": meta}
    ALERTS.append(rec); del ALERTS[:-200]  # keep last 200
    write_event("alert", level=level, title=title, **meta)
    await ws_manager.broadcast({"ts": rec["ts"], "vec": {"alert": rec}})

# ===== Rate limiting =====
def rate_check(ip: str) -> bool:
    now = int(time.time())
    buf = RATE.get(ip, [])
    buf = [t for t in buf if t > now - RATE_WINDOW]
    allowed = len(buf) < RATE_MAX
    buf.append(now); RATE[ip] = buf
    return allowed

def rate_is_hot(ip: str) -> Optional[int]:
    """Return current count if window usage >= 80% of limit."""
    cnt = len(RATE.get(ip, []))
    return cnt if cnt >= int(RATE_MAX * 0.8) else None

# ===== WS manager =====
class WSManager:
    def __init__(self) -> None: self.active: List[WebSocket] = []
    async def connect(self, ws: WebSocket): await ws.accept(); self.active.append(ws)
    async def disconnect(self, ws: WebSocket):
        if ws in self.active: self.active.remove(ws)
    async def broadcast(self, data: Dict[str, Any]):
        msg = json.dumps(data); stale=[]
        for ws in self.active:
            try: await ws.send_text(msg)
            except Exception: stale.append(ws)
        for ws in stale:
            try: await ws.close()
            except Exception: pass
            await self.disconnect(ws)

ws_manager = WSManager()

# ===== FastAPI =====
app = FastAPI(title="MeCloneMe API (mini)")
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_credentials=True, allow_methods=['*'], allow_headers=['*'])
load_stores()

# ===== HTTP middleware: audit + metrics =====
@app.middleware("http")
async def audit_mw(request: Request, call_next):
    global REQ_COUNT
    t0 = time.perf_counter()
    ip = request.client.host if request.client else "?"
    ua = (request.headers.get("user-agent") or "")[:160]
    path = request.url.path; method = request.method
    try:
        response = await call_next(request)
        status = getattr(response, "status_code", 200)
        return response
    finally:
        ms = int((time.perf_counter() - t0) * 1000); REQ_COUNT += 1
        write_event("http", ip=ip, ua=ua, method=method, path=path, status=status, ms=ms)
        if status >= 500:
            await emit_alert("crit", "HTTP 5xx", cooldown_s=10, path=path, code=status)
        hot = rate_is_hot(ip)
        if hot is not None:
            await emit_alert("warn", "Rate hot", cooldown_s=15, ip=ip, count=hot, limit=RATE_MAX)

# ===== Helpers =====
def _quantiles(vals: List[int]) -> Dict[str, int]:
    if not vals: return {"p50":0,"p95":0,"avg":0,"max":0}
    p50 = int(statistics.quantiles(vals, n=100)[49]) if len(vals) >= 2 else vals[0]
    p95_idx = max(0, int(len(vals)*0.95) - 1); p95 = sorted(vals)[p95_idx]
    avg = int(sum(vals)/len(vals))
    return {"p50":p50, "p95":p95, "avg":avg, "max":max(vals)}

def _compute_summary(n: int) -> Dict[str, Any]:
    rows = [r for r in tail_jsonl(EVENTS_JSONL, n) if r.get("kind")=="http"]
    total=len(rows); by_path={}; by_method={}; by_status={}; lat=[]
    for r in rows:
        p=r.get("path","?"); by_path[p]=by_path.get(p,0)+1
        m=r.get("method","?"); by_method[m]=by_method.get(m,0)+1
        s=str(r.get("status","?")); by_status[s]=by_status.get(s,0)+1
        if isinstance(r.get("ms"), int): lat.append(r["ms"])
    top_paths=sorted(by_path.items(), key=lambda x:x[1], reverse=True)[:5]
    lat_q=_quantiles(lat); err4=sum(v for k,v in by_status.items() if k.startswith("4")); err5=sum(v for k,v in by_status.items() if k.startswith("5"))
    return {"ok": True, "total": total, "paths_top5": top_paths, "methods": by_method, "statuses": by_status, "latency_ms": lat_q, "errors":{"4xx":err4,"5xx":err5}}

def _compute_rollup(minutes: int, n: int) -> Dict[str, Any]:
    now=int(time.time()); floor_start = now - minutes*60
    rows=[r for r in tail_jsonl(EVENTS_JSONL, n) if r.get("kind")=="http" and r.get("ts",0)>=floor_start]
    buckets: Dict[int, List[int]]={}; counts: Dict[int,int]={}
    for r in rows:
        m=(int(r.get("ts",0))//60)*60; buckets.setdefault(m, []).append(int(r.get("ms",0))); counts[m]=counts.get(m,0)+1
    series=[]
    for m in range((floor_start//60)*60, (now//60)*60 + 60, 60):
        vals=buckets.get(m, []); q=_quantiles(vals) if vals else {"p50":0,"p95":0,"avg":0,"max":0}
        series.append({"t":m,"p95":q["p95"],"count":counts.get(m,0)})
    return {"ok":True, "series":series[-minutes:]}

def _compute_health(n: int) -> Dict[str, Any]:
    rows=[r for r in tail_jsonl(EVENTS_JSONL, n) if r.get("kind")=="http"]
    lat=[r["ms"] for r in rows if isinstance(r.get("ms"), int)]; lat_q=_quantiles(lat); codes={}
    for r in rows: k=str(r.get("status","?")); codes[k]=codes.get(k,0)+1
    level="ok"; 
    if lat_q["p95"]>=P95_CRIT: level="crit"
    elif lat_q["p95"]>=P95_WARN: level="warn"
    return {"ok":True, "ts":int(time.time()), "version":API_VERSION, "uptime_s":int(time.time())-BOOT_TS,
            "req_count":REQ_COUNT, "rate_window_s":RATE_WINDOW, "latency_ms":lat_q, "codes":codes,
            "sample_size":len(rows), "level":level, "thresholds":{"warn":P95_WARN,"crit":P95_CRIT}}

# ===== UI roots =====
@app.get("/", response_class=HTMLResponse)
def root(): return HTMLResponse(PANEL_HTML)

@app.get("/mobile", response_class=HTMLResponse)
def mobile_page(): return HTMLResponse(MOBILE_HTML)

# ===== Simple health/metrics =====
@app.get("/healthz")
def healthz(): return {"ok": True, "ts": int(time.time())}

@app.get("/api/health")
def api_health():
    return {"ok":True, "version":API_VERSION, "ts":int(time.time()),
            "uptime":int(time.time())-BOOT_TS,
            "counts":{"requests":REQ_COUNT,"nonces":len(NONCES),"pubkeys":len(PUBKEYS),"sessions":len(SESSIONS)}}

@app.get("/api/health/detail")
def health_detail(n: int = Query(300, ge=50, le=10000)):
    return _compute_health(n)

@app.get("/api/version")
def api_version():
    git=os.getenv("RENDER_GIT_COMMIT") or os.getenv("GIT_SHA") or ""
    svc=os.getenv("RENDER_SERVICE_NAME") or ""
    return {"ok":True, "version":API_VERSION, "boot_ts":BOOT_TS, "git":git[:12], "service":svc}

@app.get("/api/metrics")
def api_metrics(): return {"ok":True, "rate":{"window_s":RATE_WINDOW,"max":RATE_MAX}, "counts":{"ip_slots":len(RATE)}}

# ===== WS + Shadow ingest =====
class ShadowFrame(BaseModel): ts:int; vec:Dict[str,Any]

@app.websocket("/shadow/ws")
async def ws_shadow(ws: WebSocket):
    await ws_manager.connect(ws)
    try:
        while True: await ws.receive_text()
    except WebSocketDisconnect:
        await ws_manager.disconnect(ws)

@app.post("/shadow/ingest")
async def shadow_ingest(frame: ShadowFrame):
    try:
        with open(SHADOW_JSONL,"a",encoding="utf-8") as f: f.write(json.dumps(frame.dict())+"\n")
    except Exception: pass
    await ws_manager.broadcast(frame.dict()); write_event("shadow", **frame.dict())
    return {"ok":True}

# ===== Auth flow =====
class PubKeyReq(BaseModel): kid:str; pub:str
class VerifyReq(BaseModel): jws:str

def bad(reason: str, **extra): data={"ok":False,"reason":reason}; data.update(extra); return JSONResponse(data)
def ok(**payload): data={"ok":True}; data.update(payload); return JSONResponse(data)

def require_session(token: str) -> Optional[Dict[str, Any]]:
    if not token or not token.startswith("sess_"): return None
    s=SESSIONS.get(token); 
    if not s: return None
    if s["exp"]<int(time.time()): SESSIONS.pop(token,None); save_sessions(); return None
    return s

def _auth_token(request: Request) -> str:
    auth=request.headers.get("authorization") or ""
    return auth.split(" ",1)[1] if auth.lower().startswith("bearer ") else ""

@app.get("/auth/challenge")
async def challenge(request: Request, aud: str = "mobile"):
    ip=request.client.host if request.client else "?"
    if not rate_check(ip):
        await emit("rate", ip=ip, status="limit"); write_event("rate_limit", ip=ip, path="/auth/challenge")
        await emit_alert("crit","Rate limit", path="/auth/challenge", ip=ip)
        return bad("rate-limit")
    now=int(time.time()); nonce=secrets.token_hex(16); NONCES[nonce]=now+NONCE_TTL
    for n,exp in list(NONCES.items()):
        if exp<now: NONCES.pop(n,None)
    await emit("challenge", aud=aud, nonce=nonce); write_event("auth.challenge", ip=ip, aud=aud, nonce=nonce)
    hot=rate_is_hot(ip)
    if hot is not None: await emit_alert("warn","Rate hot", ip=ip, count=hot, limit=RATE_MAX)
    return ok(aud=aud, nonce=nonce, ttl=NONCE_TTL)

@app.post("/guardian/register_pubkey")
async def register_pubkey(req: PubKeyReq, request: Request):
    ip=request.client.host if request.client else "?"
    if not rate_check(ip):
        await emit("rate", ip=ip, status="limit"); write_event("rate_limit", ip=ip, path="/guardian/register_pubkey")
        await emit_alert("crit","Rate limit", path="/guardian/register_pubkey", ip=ip)
        return bad("rate-limit")
    try:
        if len(b64u_decode(req.pub))!=32: return bad("bad-pubkey")
    except Exception: return bad("bad-pubkey")
    PUBKEYS[req.kid]=req.pub; save_pubkeys()
    await emit("admin", action="register_pubkey", kid=req.kid); write_event("auth.register_pubkey", kid=req.kid)
    return ok(registered=list(PUBKEYS.keys()))

@app.post("/guardian/verify")
async def guardian_verify(request: Request, req: VerifyReq):
    ip=request.client.host if request.client else "?"
    if not rate_check(ip):
        await emit("rate", ip=ip, status="limit"); write_event("rate_limit", ip=ip, path="/guardian/verify")
        await emit_alert("crit","Rate limit", path="/guardian/verify", ip=ip)
        return bad("rate-limit")
    try:
        parts=req.jws.split("."); 
        if len(parts)!=3: return bad("bad-format")
        h_b,p_b,s_b=parts; header=json.loads(b64u_decode(h_b)); payload=json.loads(b64u_decode(p_b)); sig=b64u_decode(s_b)
    except Exception: return bad("bad-jws")
    kid=header.get("kid"); alg=header.get("alg")
    if alg!="EdDSA" or not kid or kid not in PUBKEYS: return bad("bad-header")
    try:
        vk=VerifyKey(b64u_decode(PUBKEYS[kid])); vk.verify((h_b+"."+p_b).encode(), sig)
    except BadSignatureError: return bad("bad-signature")
    now=int(time.time())
    try:
        if abs(now-int(payload["ts"]))>NONCE_TTL: return bad("nonce-expired")
        aud=payload.get("aud"); nonce=payload.get("nonce")
        if (not aud) or (not nonce): return bad("missing-claims")
        exp=NONCES.get(nonce); if not exp or exp<now: return bad("nonce-expired")
        NONCES.pop(nonce,None)
    except Exception: return bad("bad-payload")
    sid="sess_"+secrets.token_hex(16); sess_exp=now+SESSION_TTL; SESSIONS[sid]={"kid":kid,"exp":sess_exp}; save_sessions()
    await emit("auth", status="ok", aud=aud, kid=kid); write_event("auth.verify_ok", kid=kid, aud=aud, sid=sid)
    return ok(payload=payload, session=sid, exp=sess_exp)

@app.get("/protected/hello")
async def protected_hello(request: Request):
    ip=request.client.host if request.client else "?"
    if not rate_check(ip):
        await emit("rate", ip=ip, status="limit"); write_event("rate_limit", ip=ip, path="/protected/hello")
        await emit_alert("crit","Rate limit", path="/protected/hello", ip=ip)
        return bad("rate-limit")
    token=_auth_token(request); sess=require_session(token)
    if not sess:
        await emit("unauth", path="/protected/hello", ip=ip); write_event("auth.unauthorized", ip=ip, path="/protected/hello")
        return bad("unauthorized")
    await emit("hello", kid=sess["kid"]); write_event("hello", kid=sess["kid"])
    return ok(msg="hello dev-user", kid=sess["kid"], exp=sess["exp"])

@app.post("/guardian/refresh")
async def refresh(request: Request):
    token=_auth_token(request); sess=require_session(token)
    if not sess:
        await emit("unauth", path="/guardian/refresh"); return bad("unauthorized")
    sess["exp"]=int(time.time())+SESSION_TTL; save_sessions()
    await emit("session", action="refresh", kid=sess["kid"], exp=sess["exp"]); write_event("auth.refresh", kid=sess["kid"], exp=sess["exp"])
    return ok(exp=sess["exp"])

@app.post("/guardian/logout")
async def logout(request: Request):
    token=_auth_token(request); s=SESSIONS.pop(token, None); save_sessions()
    if s: await emit("session", action="logout", kid=s["kid"]); write_event("auth.logout", kid=s["kid"])
    return ok()

# ===== Admin: events =====
@app.get("/admin/events/tail")
def admin_tail(request: Request, n: int = Query(200, ge=1, le=2000)):
    token=_auth_token(request)
    if not require_session(token): return bad("unauthorized")
    return {"ok":True, "items": tail_jsonl(EVENTS_JSONL, n)}

def _flatten(d: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
    out={}
    for k,v in d.items():
        kk=f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict): out.update(_flatten(v, kk))
        else: out[kk]=v
    return out

@app.get("/admin/events/export.csv")
def admin_csv(request: Request, n: int = Query(500, ge=1, le=5000)):
    token=_auth_token(request)
    if not require_session(token): return bad("unauthorized")
    rows=tail_jsonl(EVENTS_JSONL, n); headers=[]; flat_rows=[]
    for r in rows:
        fr=_flatten(r); flat_rows.append(fr)
        for k in fr.keys():
            if k not in headers: headers.append(k)
    buf=io.StringIO(); buf.write(",".join(headers)+"\n")
    for fr in flat_rows:
        vals=[]
        for h in headers:
            v=fr.get(h,"")
            if isinstance(v,(dict,list)): v=json.dumps(v,ensure_ascii=False)
            s=str(v).replace('"','""')
            if any(c in s for c in [",","\n",'"']): s=f'"{s}"'
            vals.append(s)
        buf.write(",".join(vals)+"\n")
    return PlainTextResponse(buf.getvalue(), media_type="text/csv")

@app.get("/admin/events/search")
def admin_search(request: Request,
                 n: int = Query(1000, ge=10, le=10000),
                 path_re: str = "",
                 status: str = "",
                 method: str = ""):
    token=_auth_token(request)
    if not require_session(token): return bad("unauthorized")
    rows=tail_jsonl(EVENTS_JSONL, n)
    rex = re.compile(path_re) if path_re else None
    def status_match(code: int) -> bool:
        if not status: return True
        s=str(status).lower()
        if s in ("2xx","4xx","5xx"): return str(code).startswith(s[0])
        try: return int(s)==int(code)
        except: return False
    out=[]
    for r in rows:
        if r.get("kind")!="http": continue
        if method and r.get("method","").upper()!=method.upper(): continue
        if rex and not rex.search(r.get("path","")): continue
        if not status_match(int(r.get("status",0))): continue
        out.append(r)
    return {"ok":True, "items": out}

@app.post("/admin/events/purge")
def admin_purge(request: Request):
    token=_auth_token(request)
    if not require_session(token): return bad("unauthorized")
    try:
        if os.path.exists(EVENTS_JSONL):
            ts=int(time.time()); os.replace(EVENTS_JSONL, f"{EVENTS_JSONL}.{ts}.bak")
        open(EVENTS_JSONL,"w",encoding="utf-8").close(); write_event("admin.purge"); return ok(msg="purged")
    except Exception as e:
        return bad("purge-failed", error=str(e))

# >>> NEW: /admin/events/ingest (pojedynczy lub lista) <<<
@app.post("/admin/events/ingest")
async def admin_ingest(request: Request):
    token=_auth_token(request)
    if not require_session(token): return bad("unauthorized")
    try:
        body = await request.json()
    except Exception:
        return bad("bad-json")
    def store(evt: Dict[str, Any]):
        if not isinstance(evt, dict): return
        kind = evt.get("kind","ext")
        data = dict(evt); data.pop("kind", None)
        write_event(kind, **data)
    if isinstance(body, list):
        for e in body: store(e)
        return ok(stored=len(body))
    store(body); return ok(stored=1)

# ===== Snapshots („notebooks”) =====
@app.post("/admin/snaps/save")
def snaps_save(request: Request, minutes: int = Query(60, ge=5, le=1440), n: int = Query(5000, ge=200, le=50000)):
    token=_auth_token(request)
    if not require_session(token): return bad("unauthorized")
    snap={"ts":int(time.time()),"version":API_VERSION,"summary":_compute_summary(min(n,10000)),
          "rollup":_compute_rollup(minutes,n),"health":_compute_health(max(200,min(n,5000)))}
    name=time.strftime("snap-%Y%m%d-%H%M%S.json", time.gmtime(snap["ts"])); path=os.path.join(SNAPS_DIR,name)
    try:
        with open(path,"w",encoding="utf-8") as f: json.dump(snap,f,ensure_ascii=False)
        write_event("admin.snap_save", file=name); return ok(file=name)
    except Exception as e: return bad("snap-save-failed", error=str(e))

@app.get("/admin/snaps/list")
def snaps_list(request: Request):
    token=_auth_token(request)
    if not require_session(token): return bad("unauthorized")
    files=sorted([fn for fn in os.listdir(SNAPS_DIR) if fn.endswith(".json")])
    return ok(files=files)

@app.get("/admin/snaps/get")
def snaps_get(request: Request, name: str):
    token=_auth_token(request)
    if not require_session(token): return bad("unauthorized")
    safe=os.path.basename(name); path=os.path.join(SNAPS_DIR,safe)
    try:
        with open(path,"r",encoding="utf-8") as f: data=json.load(f)
        return JSONResponse(data)
    except Exception as e: return bad("snap-not-found", error=str(e))

# ===== Logs APIs: summary/rollups/heatmaps =====
@app.get("/api/logs/summary")
def logs_summary(n: int = Query(1000, ge=10, le=10000)): return _compute_summary(n)

@app.get("/api/logs/rollup")
def logs_rollup(minutes: int = Query(60, ge=5, le=1440), n: int = Query(10000, ge=100, le=50000)): return _compute_rollup(minutes, n)

@app.get("/api/logs/heatmap")
def logs_heatmap(minutes: int = Query(60, ge=5, le=1440), n: int = Query(20000, ge=200, le=60000)):
    roll=_compute_rollup(minutes, n)["series"]; maxc=max([c["count"] for c in roll], default=0)
    cells=[{"t":c["t"],"count":c["count"]} for c in roll]
    return {"ok":True, "minutes":minutes, "max":maxc, "cells":cells}

@app.get("/api/logs/heatmap_status")
def logs_heatmap_status(minutes: int = Query(60, ge=5, le=1440), n: int = Query(30000, ge=200, le=80000)):
    now=int(time.time()); floor_start=now-minutes*60
    rows=[r for r in tail_jsonl(EVENTS_JSONL, n) if r.get("kind")=="http" and r.get("ts",0)>=floor_start]
    idx: Dict[int, Dict[str,int]]={}
    for r in rows:
        m=(int(r.get("ts",0))//60)*60; idx.setdefault(m,{"2":0,"4":0,"5":0,"T":0})
        code=str(r.get("status","200"))
        if code.startswith("5"): idx[m]["5"]+=1
        elif code.startswith("4"): idx[m]["4"]+=1
        else: idx[m]["2"]+=1
        idx[m]["T"]+=1
    series=[]; maxc=0
    for m in range((floor_start//60)*60, (now//60)*60 + 60, 60):
        b=idx.get(m, {"2":0,"4":0,"5":0,"T":0})
        series.append({"t":m,"count":b["T"],"c2xx":b["2"],"c4xx":b["4"],"c5xx":b["5"]}); maxc=max(maxc, b["T"])
    return {"ok":True, "minutes":minutes, "max":maxc, "cells":series[-minutes:]}

# ===== Rate window stats =====
@app.get("/api/rate/window_stats")
def rate_window_stats(top: int = Query(10, ge=1, le=100)):
    now=int(time.time()); out=[]
    for ip,buf in list(RATE.items()):
        pruned=[t for t in buf if t > now - RATE_WINDOW]; RATE[ip]=pruned
        if pruned: out.append({"ip":ip, "count":len(pruned)})
    out.sort(key=lambda x:x["count"], reverse=True)
    return {"ok":True, "now":now, "window_s":RATE_WINDOW, "max":RATE_MAX, "total_ips":len(out), "ips":out[:top]}

# ===== Alerts polling =====
@app.get("/api/alerts/poll")
def alerts_poll(since: int = 0, limit: int = Query(30, ge=1, le=100)):
    items=[a for a in ALERTS if a["ts"]>since]
    return {"ok":True, "items": items[-limit:]}

# ===== DIAG: echo public + signed =====
class JWSReq(BaseModel): jws:str

@app.get("/api/diag/echo")
def diag_echo(request: Request):
    headers={}
    for k,v in request.headers.items():
        kl=k.lower(); headers[k]="***" if kl in ("authorization","cookie") else v
    return {"ok":True, "ts":int(time.time()), "ip":request.client.host if request.client else "?",
            "method":request.method, "path":request.url.path, "query":dict(request.query_params),
            "headers":headers, "ua":request.headers.get("user-agent","")}

@app.post("/api/diag/echo_signed")
def diag_echo_signed(req: JWSReq, request: Request):
    try:
        h_b,p_b,s_b=req.jws.split("."); header=json.loads(b64u_decode(h_b)); payload=json.loads(b64u_decode(p_b)); sig=b64u_decode(s_b)
    except Exception: return bad("bad-jws")
    kid=header.get("kid"); alg=header.get("alg")
    if alg!="EdDSA" or not kid or kid not in PUBKEYS: return bad("bad-header")
    try:
        vk=VerifyKey(b64u_decode(PUBKEYS[kid])); vk.verify((h_b+"."+p_b).encode(), sig)
    except BadSignatureError: return bad("bad-signature")
    now=int(time.time())
    try:
        if abs(now-int(payload["ts"]))>NONCE_TTL: return bad("nonce-expired")
        if payload.get("aud")!="diag": return bad("bad-aud")
        nonce=payload.get("nonce"); exp=NONCES.get(nonce)
        if not exp or exp<now: return bad("nonce-expired")
        NONCES.pop(nonce,None)
    except Exception: return bad("bad-payload")
    base=diag_echo(request); base["verified"]=True; base["kid"]=kid; base["payload"]=payload
    return base

# ===== Prometheus exporter =====
@app.get("/metrics")
def metrics():
    # Snapshot (ostatnie 1000 zdarzeń)
    summ=_compute_summary(1000); health=_compute_health(300)
    lines=[]
    def h(x): lines.append(x)
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
    for code,count in (summ["statuses"] or {}).items():
        h(f'mecloneme_http_status_recent_total{{code="{code}"}} {count}')
    body="\n".join(lines)+"\n"
    return PlainTextResponse(body, media_type="text/plain; version=0.0.4")

# ===== AR engine (stub) =====
@app.get("/ar/ping")
def ar_ping(): return {"ok":True, "engine":"stub", "ts":int(time.time())}

# ===== HTML (panel + mobile) =====
PANEL_HTML = """<!doctype html>
<meta charset="utf-8"><title>Guardian — mini panel</title>
<style>
  :root{ --ok:#2e7d32; --warn:#e09100; --crit:#c62828; --btn:#f3f3f3; --bd:#e5e5e5; }
  body{font-family: system-ui,-apple-system,Segoe UI,Roboto;margin:16px}
  .card{border:1px solid var(--bd);border-radius:8px;padding:12px}
  .btn{padding:6px 10px;border:1px solid var(--bd);border-radius:8px;background:var(--btn);cursor:pointer}
  .btn[disabled]{opacity:.6;cursor:not-allowed}
  .btn.active{outline:2px solid var(--ok);background:#edf7ed}
  .btn.danger{border-color:var(--crit);color:var(--crit)}
  .btn.danger.active{outline:2px solid var(--crit);background:#fdeaea}
  #alert{display:none;border-radius:8px;padding:10px;margin:8px 0}
  #alert.warn{display:block;background:#fff4e5;border:1px solid #ffe3b3}
  #alert.crit{display:block;background:#fdeaea;border:1px solid #f5b7b1}
  #toast{position:fixed;right:16px;bottom:16px;background:#111;color:#fff;padding:8px 12px;border-radius:8px;opacity:0;transition:opacity .2s}
  #toast.show{opacity:.9}
  pre{background:#f7f7f7;border:1px solid #eee;border-radius:8px;padding:12px}
  .grid{display:grid;grid-template-columns:repeat(60,1fr);gap:2px}
  .cell{height:18px;border-radius:3px;background:#eef5ee}
  .pill{display:inline-block;padding:2px 6px;border-radius:10px;font-size:12px;color:#fff}
  .pill.warn{background:#e09100}.pill.crit{background:#c62828}.pill.info{background:#2e7d32}
</style>
<body>
<h1>Guardian — mini panel</h1>

<div id="alert"><b>STATUS:</b> <span id="alertText"></span></div>

<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
  <div class="card">
    <h2>Challenge</h2>
    <pre id="challenge" style="min-height:120px"></pre>
  </div>
  <div class="card">
    <h2>Live log <span id="ws" style="color:var(--ok)">WS: connected</span></h2>
    <pre id="log" style="height:220px;overflow:auto"></pre>
  </div>
</div>

<div class="card" style="margin-top:16px">
  <h2>Postęp projektu (tylko lokalnie — zapis w przeglądarce)</h2>
  <div id="bars"></div>
  <div style="margin-top:8px">
    <button id="save" class="btn" data-group="progress">💾 Zapisz</button>
    <button id="reset" class="btn" data-group="progress">↩︎ Reset</button>
  </div>
</div>

<div class="card" style="margin-top:16px">
  <h2>CEO — N18 widżet (live) <span style="font-size:12px;color:#666">(Kiosk mode + autorefresh)</span></h2>
  <div style="display:flex;gap:8px;flex-wrap:wrap;margin:6px 0">
    <button id="kiosk" class="btn" data-group="n18">🖥️ Kiosk: OFF</button>
    <button id="fs" class="btn" data-group="n18">⛶ Fullscreen</button>
  </div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
    <div>
      <canvas id="spark" width="640" height="90" style="width:100%;border:1px solid #eee;border-radius:6px"></canvas>
      <div style="font-size:12px;color:#666;margin-top:4px">Sparkline: p95/ms per-minute (ostatnie 60 min). Auto-refresh 5s.</div>
      <div style="display:flex;gap:8px;margin-top:8px">
        <button id="start" class="btn" data-group="n18">▶︎ Start</button>
        <button id="stop" class="btn" data-group="n18">⏸ Stop</button>
        <button id="once" class="btn" data-group="n18">↻ Odśwież teraz</button>
      </div>
      <pre id="healthOut" style="min-height:80px;margin-top:8px"></pre>
    </div>
    <div>
      <div style="display:flex;gap:16px;align-items:center;margin-bottom:6px">
        <div><b>Uptime:</b> <span id="uptime">-</span></div>
        <div><b>p95:</b> <span id="p95">-</span> ms</div>
        <div><b>4xx:</b> <span id="e4">0</span></div>
        <div><b>5xx:</b> <span id="e5">0</span></div>
      </div>
      <b>Top ścieżki</b>
      <ol id="topPaths" style="margin-top:6px"></ol>
      <b>Statusy</b>
      <pre id="codes" style="min-height:60px"></pre>
      <b>Latencja (ms)</b>
      <div id="latStats" style="font-family:ui-monospace, Menlo, monospace;"></div>
    </div>
  </div>
</div>

<div class="card" style="margin-top:16px">
  <h2>Rate heatmap (60m)</h2>
  <div id="heatmap" class="grid"></div>
  <div style="font-size:12px;color:#666;margin-top:6px">Intensywność = liczba żądań/min. Najciemniejsze = max w oknie.</div>
</div>

<div class="card" style="margin-top:16px">
  <h2>Heatmap status (60m)</h2>
  <div id="heatmapS" class="grid"></div>
  <div style="font-size:12px;color:#666;margin-top:6px">Kolor: zielony=2xx, pomarańczowy=4xx, czerwony=5xx (intensywność ~ liczba żądań).</div>
</div>

<div class="card" style="margin-top:16px">
  <h2>Rate-limiter monitor</h2>
  <div id="rateBox" style="font-family:ui-monospace,Menlo,monospace"></div>
</div>

<div class="card" style="margin-top:16px">
  <h2>Alerts & signals <small>(WS + poll)</small>
    <button id="mute" class="btn" data-group="alerts">🔈 Mute: OFF</button>
  </h2>
  <ol id="alerts"></ol>
</div>

<div class="card" style="margin-top:16px">
  <h2>Diag & Snapshots</h2>
  <div style="margin:6px 0">
    <div>Token (Bearer) do snapshotów (z <code>/mobile</code> po verify):</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:6px">
      <input id="admTok" placeholder="sess_xxx" style="width:320px">
      <button id="tokLoad" class="btn" data-group="diag">📥 Use stored</button>
      <button id="tokSave" class="btn" data-group="diag">💾 Save</button>
      <button id="tokClear" class="btn" data-group="diag">🗑️ Clear</button>
    </div>
  </div>
  <div style="display:flex;gap:8px;flex-wrap:wrap;margin:6px 0">
    <button id="btnEcho" class="btn" data-group="diag">🔎 Echo</button>
    <button id="btnEchoCurl" class="btn" data-group="diag">📋 Copy cURL: Echo</button>
    <button id="btnSnapSave" class="btn" data-group="diag">💾 Save snapshot</button>
    <button id="btnSnapList" class="btn" data-group="diag">📚 List snapshots</button>
    <button id="btnSnapGet" class="btn" data-group="diag">⬇️ Download last</button>
    <button id="btnMetricsCurl" class="btn" data-group="diag">📋 Copy cURL: /metrics</button>
    <button id="btnMetricsOpen" class="btn" data-group="diag">🔗 Open /metrics</button>
  </div>
  <pre id="diagOut" style="min-height:120px"></pre>
</div>

<div class="card" style="margin-top:16px">
  <h2>Admin — szybkie testy</h2>
  <div style="display:flex;gap:8px;flex-wrap:wrap;margin:6px 0">
    <button id="btnTail" class="btn" data-group="admin">📜 Tail (50)</button>
    <button id="btnTailCurl" class="btn" data-group="admin">📋 Copy cURL: Tail</button>
    <button id="btnCSV" class="btn" data-group="admin">⬇️ CSV (200)</button>
    <button id="btnCSVCurl" class="btn" data-group="admin">📋 Copy cURL: CSV</button>
    <button id="btnSummary" class="btn" data-group="admin">📊 Summary (500)</button>
    <button id="btnHealth" class="btn" data-group="admin">🩺 Health detail</button>
    <button id="btnPurge" class="btn danger" data-group="admin">🧹 Purge log</button>
    <button id="btnPurgeCurl" class="btn danger" data-group="admin">📋 Copy cURL: Purge</button>
  </div>
  <div style="display:flex;gap:8px;align-items:center;margin:6px 0">
    <input id="re" placeholder="path regex (np. ^/api/)" style="width:240px">
    <input id="st" placeholder="status (2xx|4xx|5xx|200)" style="width:160px">
    <input id="md" placeholder="method (GET|POST)" style="width:140px">
    <input id="nSearch" type="number" value="500" style="width:90px">
    <button id="btnSearch" class="btn" data-group="admin">🔍 Search</button>
    <button id="btnSearchCurl" class="btn" data-group="admin">📋 Copy cURL: Search</button>
  </div>
  <pre id="admOut" style="min-height:120px"></pre>
</div>

<div id="toast"></div>

<script>
const $=id=>document.getElementById(id);
function toast(msg){ const t=$("toast"); t.textContent=msg; t.classList.add("show"); setTimeout(()=>t.classList.remove("show"), 1200); }
function setActive(button){ const group=button.getAttribute("data-group"); if(!group) return; document.querySelectorAll('[data-group="'+group+'"]').forEach(b=>b.classList.remove("active")); button.classList.add("active"); }

// === Token store (multi-env) ===
const TOK_SINGLE="guardian_session";
const TOKMAP_KEY="guardian_tokens";
function getTokMap(){ try{return JSON.parse(localStorage.getItem(TOKMAP_KEY)||"{}")}catch(_){return{}} }
function setTokForOrigin(token){ const m=getTokMap(); m[location.origin]=token; localStorage.setItem(TOKMAP_KEY, JSON.stringify(m)); }
function getTokForOrigin(){ return getTokMap()[location.origin] || "" }

// === Kiosk helpers ===
let wake=null;
async function wakeOn(){ try{ if("wakeLock" in navigator){ wake = await navigator.wakeLock.request("screen"); wake.addEventListener?.("release",()=>{});} }catch(e){} }
async function goFullscreen(){ try{ await (document.documentElement.requestFullscreen?.()||Promise.resolve()); }catch(e){} }

// === Progress bars ===
const LS_KEY="guardian_progress";
const FIELDS=[["Guardian/Auth","ga"],["AR Engine (R&D)","ar"],["App Shell / UI","ui"],["Cloud & Deploy","cd"],["MVP (całość)","mvp"]];
function renderBars(state){ const root=$("bars"); root.innerHTML="";
  FIELDS.forEach(([label,key])=>{ const wrap=document.createElement("div");
    wrap.style.display="grid";wrap.style.gridTemplateColumns="200px 1fr 42px 42px";wrap.style.gap="8px";wrap.style.alignItems="center";wrap.style.margin="6px 0";
    const lab=document.createElement("div"); lab.textContent=label;
    const bar=document.createElement("div"); bar.style.height="8px";bar.style.background="#eee";bar.style.borderRadius="4px";
    const inner=document.createElement("div"); inner.style.height="100%";inner.style.width=(state[key]||0)+"%";inner.style.background="var(--ok)";inner.style.borderRadius="4px";bar.appendChild(inner);
    const input=document.createElement("input"); input.type="number";input.min=0;input.max=100;input.value=state[key]||0;input.style.width="42px";
    const pct=document.createElement("div"); pct.textContent=(state[key]||0)+"%";
    input.oninput=()=>{let v=Math.max(0,Math.min(100,parseInt(input.value||"0",10)));inner.style.width=v+"%";pct.textContent=v+"%";state[key]=v;};
    wrap.append(lab,bar,input,pct); root.appendChild(wrap); });
}

// Sparkline + loaders
let timer=null; const series=[];
function drawSpark(values){ const cv=$("spark"); const ctx=cv.getContext("2d"); ctx.clearRect(0,0,cv.width,cv.height);
  if(values.length<2) return; const pad=6, w=cv.width-pad*2, h=cv.height-pad*2; const min=Math.min(...values), max=Math.max(...values);
  const norm=v => (max===min?0.5:(v-min)/(max-min)); ctx.beginPath();
  values.forEach((v,i)=>{ const x=pad + (i/(values.length-1))*w; const y=pad + (1-norm(v))*h; if(i===0) ctx.moveTo(x,y); else ctx.lineTo(x,y); });
  ctx.lineWidth=2; ctx.strokeStyle="var(--ok)"; ctx.stroke(); }

function renderHeatmap(cells,max){ const root=$("heatmap"); root.innerHTML=""; cells.forEach(c=>{ const div=document.createElement("div"); div.className="cell";
  const a=max? (c.count/max):0; div.style.background=`rgba(46,125,50, ${0.1+0.9*a})`; div.title=new Date(c.t*1000).toLocaleTimeString()+" – "+c.count+" req/min"; root.appendChild(div); }); }

function renderHeatmapS(cells,max){ const root=$("heatmapS"); root.innerHTML="";
  cells.forEach(c=>{ const div=document.createElement("div"); div.className="cell";
    let rgb="46,125,50"; if(c.c5xx>0) rgb="198,40,40"; else if(c.c4xx>0) rgb="224,145,0";
    const a=max? (c.count/max):0; div.style.background=`rgba(${rgb}, ${0.15+0.85*a})`;
    div.title=new Date(c.t*1000).toLocaleTimeString()+` – 2xx:${c.c2xx} 4xx:${c.c4xx} 5xx:${c.c5xx}`; root.appendChild(div); }); }

function renderRateBox(stats){ const box=$("rateBox"); const lines=[];
  lines.push(`window=${stats.window_s}s, limit=${stats.max}/ip, ips=${stats.total_ips}`);
  (stats.ips||[]).forEach((r,i)=>{ const bar="█".repeat(Math.max(1, Math.round((r.count/stats.max)*10))); lines.push(`${i+1}. ${r.ip.padEnd(15)} ${String(r.count).padStart(3)} ${bar}`); });
  box.textContent=lines.join("\\n"); }

const TOKKEY="guardian_session"; 
$("admTok").value=localStorage.getItem(TOKKEY)||getTokForOrigin()||"";
$("admTok").oninput=()=>localStorage.setItem(TOKKEY,$("admTok").value.trim());
$("tokLoad").onclick=()=>{ setActive($("tokLoad")); const t=getTokForOrigin(); $("admTok").value=t; localStorage.setItem(TOKKEY,t); toast(t? "Załadowano token z pamięci":"Brak zapisanego."); };
$("tokSave").onclick=()=>{ setActive($("tokSave")); const t=$("admTok").value.trim(); setTokForOrigin(t); localStorage.setItem(TOKKEY,t); toast("Token zapisany"); };
$("tokClear").onclick=()=>{ setActive($("tokClear")); $("admTok").value=""; localStorage.removeItem(TOKKEY); setTokForOrigin(""); toast("Token wyczyszczony"); };

function tok(){ return $("admTok").value.trim(); }
function copy(text){ navigator.clipboard.writeText(text).then(()=>toast("Skopiowano")).catch(()=>toast("Nie udało się skopiować")); }
async function withBusy(btn, fn){ try{ btn.setAttribute("disabled",""); setActive(btn); await fn(); } finally{ btn.removeAttribute("disabled"); } }

// Alerts (WS + poll + beep)
let muted=false; let lastAlert=0;
$("mute").onclick=()=>{ muted=!muted; $("mute").textContent = muted? "🔇 Mute: ON":"🔈 Mute: OFF"; setActive($("mute")); };
function beep(){ if(muted) return; try{ const ctx=new (window.AudioContext||window.webkitAudioContext)(); const o=ctx.createOscillator(); const g=ctx.createGain();
  o.connect(g); g.connect(ctx.destination); o.type="sine"; o.frequency.setValueAtTime(880, ctx.currentTime);
  g.gain.setValueAtTime(0.0001, ctx.currentTime); g.gain.exponentialRampToValueAtTime(0.2, ctx.currentTime+0.01);
  g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime+0.25); o.start(); o.stop(ctx.currentTime+0.26); }catch(e){} }
function pushAlert(rec){ const ul=$("alerts"); const li=document.createElement("li");
  const pill=document.createElement("span"); pill.className="pill "+(rec.level||"info"); pill.textContent=rec.level.toUpperCase();
  li.appendChild(pill); li.append(" "); li.appendChild(document.createTextNode(`[${new Date(rec.ts*1000).toLocaleTimeString()}] ${rec.title}`));
  if(rec.meta){ li.appendChild(document.createElement("br")); li.appendChild(document.createTextNode(JSON.stringify(rec.meta))); }
  ul.insertBefore(li, ul.firstChild); while(ul.children.length>15) ul.removeChild(ul.lastChild); beep(); lastAlert=Math.max(lastAlert, rec.ts); }
async function pollAlerts(){ const r=await fetch('/api/alerts/poll?since='+lastAlert); const j=await r.json(); (j.items||[]).forEach(pushAlert); }

// Loaders
async function loadSummary(){ const r=await fetch('/api/logs/summary?n=500'); const j=await r.json();
  const ul=$("topPaths"); ul.innerHTML=""; (j.paths_top5||[]).forEach(([p,c])=>{ const li=document.createElement('li'); li.textContent=p+"  ("+c+")"; ul.appendChild(li); });
  $("codes").textContent=JSON.stringify(j.statuses||{},null,2);
  const lat=j.latency_ms||{}; $("latStats").textContent=`p50=${lat.p50||0}ms  p95=${lat.p95||0}ms  avg=${lat.avg||0}ms  max=${lat.max||0}ms`;
  $("p95").textContent=lat.p95||0; $("e4").textContent=(j.errors&&j.errors["4xx"])||0; $("e5").textContent=(j.errors&&j.errors["5xx"])||0; }
async function loadHealth(){ const r=await fetch('/api/health/detail?n=300'); const j=await r.json();
  $("healthOut").textContent=JSON.stringify({ts:j.ts, uptime_s:j.uptime_s, codes:j.codes, latency_ms:j.latency_ms, sample:j.sample_size},null,2);
  $("uptime").textContent=j.uptime_s+"s"; const p95=(j.latency_ms&&j.latency_ms.p95)||0; const alert=$("alert"), txt=$("alertText"); alert.className=""; alert.style.display="block";
  if(p95>=j.thresholds.crit){ alert.classList.add("crit"); txt.textContent=`CRIT: p95=${p95}ms (>= ${j.thresholds.crit})`; }
  else if(p95>=j.thresholds.warn){ alert.classList.add("warn"); txt.textContent=`WARN: p95=${p95}ms (>= ${j.thresholds.warn})`; }
  else { alert.style.display="none"; txt.textContent=""; } }
async function loadRollup(){ const r=await fetch('/api/logs/rollup?minutes=60'); const j=await r.json(); const vals=(j.series||[]).map(pt=>pt.p95||0); if(vals.length){ series.length=0; vals.forEach(v=>series.push(v)); drawSpark(series); } }
async function loadHeat(){ const r=await fetch('/api/logs/heatmap?minutes=60'); const j=await r.json(); renderHeatmap(j.cells||[], j.max||0); }
async function loadHeatStatus(){ const r=await fetch('/api/logs/heatmap_status?minutes=60'); const j=await r.json(); renderHeatmapS(j.cells||[], j.max||0); }
async function loadRateStats(){ const r=await fetch('/api/rate/window_stats?top=8'); const j=await r.json(); renderRateBox(j); }

(async function init(){
  try{ const r=await fetch('/auth/challenge'); $("challenge").textContent=JSON.stringify(await r.json(),null,2); } catch(e){ $("challenge").textContent="API offline"; }
  try{ const wsUrl=(location.protocol==="https:"?"wss":"ws")+"://"+location.host+"/shadow/ws"; const ws=new WebSocket(wsUrl);
    ws.onmessage=(ev)=>{ try{ const j=JSON.parse(ev.data); if(j.vec && j.vec.alert){ pushAlert(j.vec.alert); return; }
      const el=$("log"); el.textContent+=JSON.stringify(j)+"\\n"; el.scrollTop=el.scrollHeight; }catch(e){} }; }
  catch(e){ const el=document.querySelector("#ws"); el.textContent="WS: error"; el.style.color="#c62828"; }

  const state=JSON.parse(localStorage.getItem(LS_KEY)||"{}"); renderBars(state);
  $("save").onclick=()=>{ localStorage.setItem(LS_KEY,JSON.stringify(state)); toast("Zapisano"); };
  $("reset").onclick=()=>{ localStorage.removeItem(LS_KEY); location.reload(); };

  $("start").onclick=()=>{ if(!window._n18){ window._n18=setInterval(async()=>{ await loadHealth(); await loadSummary(); await loadRollup(); await loadHeat(); await loadHeatStatus(); await loadRateStats(); await pollAlerts(); },5000); } setActive($("start")); };
  $("stop").onclick=()=>{ if(window._n18){ clearInterval(window._n18); window._n18=null; } setActive($("stop")); };
  $("once").onclick=()=>withBusy($("once"), async()=>{ await loadHealth(); await loadSummary(); await loadRollup(); await loadHeat(); await loadHeatStatus(); await loadRateStats(); await pollAlerts(); });

  // Kiosk: pamiętaj w LS i auto-włącz
  const KIOSK_KEY="guardian_kiosk";
  function setKioskUI(on){ $("kiosk").textContent= on? "🖥️ Kiosk: ON":"🖥️ Kiosk: OFF"; }
  $("kiosk").onclick=async ()=>{ const on=localStorage.getItem(KIOSK_KEY)==="1"; const next = !on; localStorage.setItem(KIOSK_KEY", next? "1":"0"); setKioskUI(next); if(next){ $("start").click(); await wakeOn(); } setActive($("kiosk")); };
  $("fs").onclick=()=>{ setActive($("fs")); goFullscreen(); };

  setKioskUI(localStorage.getItem(KIOSK_KEY)==="1");
  if(localStorage.getItem(KIOSK_KEY)==="1"){ $("start").click(); await wakeOn(); goFullscreen(); }

  // Diag + Admin
  $("btnEcho").onclick=()=>withBusy($("btnEcho"), async()=>{ const r=await fetch('/api/diag/echo'); $("diagOut").textContent=JSON.stringify(await r.json(),null,2); });
  $("btnEchoCurl").onclick=()=>{ setActive($("btnEchoCurl")); copy(`curl "${location.origin}/api/diag/echo"`); };
  $("btnSnapSave").onclick=()=>withBusy($("btnSnapSave"), async()=>{ const r=await fetch('/admin/snaps/save',{method:'POST',headers:{Authorization:'Bearer '+tok()}}); const j=await r.json(); $("diagOut").textContent=JSON.stringify(j,null,2); if(j.file) toast("Snapshot zapisany: "+j.file); });
  $("btnSnapList").onclick=()=>withBusy($("btnSnapList"), async()=>{ const r=await fetch('/admin/snaps/list',{headers:{Authorization:'Bearer '+tok()}}); $("diagOut").textContent=JSON.stringify(await r.json(),null,2); });
  $("btnSnapGet").onclick=()=>withBusy($("btnSnapGet"), async()=>{
    const list=await (await fetch('/admin/snaps/list',{headers:{Authorization:'Bearer '+tok()}})).json();
    const files=(list.files||[]); if(!files.length){ $("diagOut").textContent="Brak snapshotów."; return; }
    const last=files[files.length-1]; const r=await fetch('/admin/snaps/get?name='+encodeURIComponent(last),{headers:{Authorization:'Bearer '+tok()}});
    const j=await r.json(); $("diagOut").textContent=JSON.stringify(j,null,2);
    const blob=new Blob([JSON.stringify(j,null,2)],{type:'application/json'}); const url=URL.createObjectURL(blob); const a=document.createElement('a'); a.href=url; a.download=last; a.click(); URL.revokeObjectURL(url); toast("Pobrano "+last);
  });
  $("btnMetricsCurl").onclick=()=>{ setActive($("btnMetricsCurl")); copy(`curl "${location.origin}/metrics"`); };
  $("btnMetricsOpen").onclick=()=>{ setActive($("btnMetricsOpen")); window.open('/metrics','_blank'); };

  const origin=location.origin;
  $("btnTail").onclick=()=>withBusy($("btnTail"), async()=>{ const r=await fetch('/admin/events/tail?n=50',{headers:{Authorization:'Bearer '+tok()}}); $("admOut").textContent=await r.text(); });
  $("btnCSV").onclick=()=>withBusy($("btnCSV"), async()=>{ const r=await fetch('/admin/events/export.csv?n=200',{headers:{Authorization:'Bearer '+tok()}}); const blob=await r.blob(); const url=URL.createObjectURL(blob); const a=document.createElement('a'); a.href=url; a.download='events.csv'; a.click(); URL.revokeObjectURL(url); $("admOut").textContent="Pobrano events.csv"; });
  $("btnSummary").onclick=()=>withBusy($("btnSummary"), async()=>{ const r=await fetch('/api/logs/summary?n=500'); $("admOut").textContent=JSON.stringify(await r.json(),null,2); });
  $("btnHealth").onclick=()=>withBusy($("btnHealth"), async()=>{ const r=await fetch('/api/health/detail?n=300'); const j=await r.json(); $("admOut").textContent=JSON.stringify(j,null,2); });
  $("btnPurge").onclick=()=>withBusy($("btnPurge"), async()=>{ if(!confirm('Na pewno usunąć zawartość events.jsonl?')) return; const r=await fetch('/admin/events/purge',{method:'POST',headers:{Authorization:'Bearer '+tok()}}); $("admOut").textContent=await r.text(); });
  $("btnTailCurl").onclick=()=>{ setActive($("btnTailCurl")); copy(`curl -H "Authorization: Bearer ${tok()}" "${origin}/admin/events/tail?n=50"`); };
  $("btnCSVCurl").onclick=()=>{ setActive($("btnCSVCurl")); copy(`curl -H "Authorization: Bearer ${tok()}" -o events.csv "${origin}/admin/events/export.csv?n=200"`); };
  $("btnPurgeCurl").onclick=()=>{ setActive($("btnPurgeCurl")); copy(`curl -X POST -H "Authorization: Bearer ${tok()}" "${origin}/admin/events/purge"`); };

  $("btnSearch").onclick=()=>withBusy($("btnSearch"), async()=>{ const qs=new URLSearchParams({n:$("nSearch").value,path_re:$("re").value,status:$("st").value,method:$("md").value}).toString();
    const r=await fetch('/admin/events/search?'+qs,{headers:{Authorization:'Bearer '+tok()}}); $("admOut").textContent=await r.text(); });
  $("btnSearchCurl").onclick=()=>{ setActive($("btnSearchCurl")); const qs=new URLSearchParams({n:$("nSearch").value,path_re:$("re").value,status:$("st").value,method:$("md").value}).toString();
    copy(`curl -H "Authorization: Bearer ${tok()}" "${origin}/admin/events/search?${qs}"`); };

  // Autostart „live”
  await loadHealth(); await loadSummary(); await loadRollup(); await loadHeat(); await loadHeatStatus(); await loadRateStats(); await pollAlerts(); $("start").click();
})();
</script>
</body>
"""

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

<div class="card" style="margin-top:10px">
  <b>6) Echo (signed, aud=diag)</b><br><br>
  <button id="getDiag">🎯 Pobierz /auth/challenge?aud=diag</button>
  <button id="echoSigned">🔐 Podpisz & /api/diag/echo_signed</button>
  <pre id="diagOut"></pre>
</div>

<script src="https://cdn.jsdelivr.net/npm/tweetnacl@1.0.3/nacl.min.js"></script>
<script>
const $=id=>document.getElementById(id);
const enc=new TextEncoder();
const b64u=b=>btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');
const fromB64u=s=>{ s=s.replace(/-/g,'+').replace(/_/g,'/'); while(s.length%4) s+='='; const bin=atob(s), out=new Uint8Array(bin.length); for(let i=0;i<bin.length;i++) out[i]=bin.charCodeAt(i); return out; };

const TOK_SINGLE="guardian_session";
const TOKMAP_KEY="guardian_tokens";
function setTokForOrigin(token){ try{ const m=JSON.parse(localStorage.getItem(TOKMAP_KEY)||"{}"); m[location.origin]=token; localStorage.setItem(TOKMAP_KEY, JSON.stringify(m)); }catch(_){ } }

const LS_KEY="guardian_priv_seed"; $("priv").value=localStorage.getItem(LS_KEY)||""; $("save").onclick=()=>{ localStorage.setItem(LS_KEY,$("priv").value.trim()); alert("Zapisano PRIV w przeglądarce."); };

function getKeypair(){ const seedB64u=$("priv").value.trim(); if(!seedB64u) throw new Error("Brak PRIV"); const seed=fromB64u(seedB64u); if(seed.length!==32) throw new Error("PRIV (seed) musi być 32 bajty!"); return nacl.sign.keyPair.fromSeed(seed); }

$("register").onclick=async ()=>{ try{ const kp=getKeypair(); const pub=b64u(kp.publicKey); const kid=$("kid").value.trim()||"dev-key-1";
  const r=await fetch("/guardian/register_pubkey",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({kid,pub})}); $("regOut").textContent=await r.text();
}catch(e){ $("regOut").textContent="ERR: "+e.message; }};

let last=null,lastDiag=null,session=null,exp=0,ticker=null; function tick(){ const eta=Math.max(0, exp - Math.floor(Date.now()/1000)); $("eta").textContent=eta+"s"; }

$("getCh").onclick=async ()=>{ const r=await fetch("/auth/challenge"); last=await r.json(); $("chOut").textContent=JSON.stringify(last,null,2); };

$("verify").onclick=async ()=>{ try{
  if(!last) throw new Error("Najpierw pobierz challenge."); const kid=$("kid").value.trim()||"dev-key-1";
  const hdr={alg:"EdDSA", typ:"JWT", kid}; const pld={aud:last.aud, nonce:last.nonce, ts: Math.floor(Date.now()/1000)};
  const h=b64u(enc.encode(JSON.stringify(hdr))); const p=b64u(enc.encode(JSON.stringify(pld))); const msg=enc.encode(h+"."+p);
  const kp=getKeypair(); const sig=nacl.sign.detached(msg, kp.secretKey); const jws=h+"."+p+"."+b64u(sig);
  const r=await fetch("/guardian/verify",{method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({jws})});
  const j=await r.json(); $("verOut").textContent=JSON.stringify(j,null,2);
  if(j.ok && j.session){ session=j.session; exp=j.exp; localStorage.setItem(TOK_SINGLE, session); setTokForOrigin(session); clearInterval(ticker); ticker=setInterval(tick,1000); tick(); }
}catch(e){ $("verOut").textContent="ERR: "+e.message; }};

$("ping").onclick=async ()=>{ const r=await fetch("/protected/hello",{ headers:{"Authorization":"Bearer "+(session||"")}}); $("pingOut").textContent=await r.text(); };
$("refresh").onclick=async ()=>{ const r=await fetch("/guardian/refresh",{ method:"POST", headers:{"Authorization":"Bearer "+(session||"")}}); const j=await r.json(); if(j.ok){ exp=j.exp; } $("pingOut").textContent=JSON.stringify(j,null,2); };
$("logout").onclick=async ()=>{ const r=await fetch("/guardian/logout",{ method:"POST", headers:{"Authorization":"Bearer "+(session||"")}}); const j=await r.json(); session=null; exp=0; tick(); $("pingOut").textContent=JSON.stringify(j,null,2); };

$("getDiag").onclick=async ()=>{ const r=await fetch("/auth/challenge?aud=diag"); lastDiag=await r.json(); $("diagOut").textContent=JSON.stringify(lastDiag,null,2); };
$("echoSigned").onclick=async ()=>{ try{
  if(!lastDiag) throw new Error("Najpierw pobierz challenge (aud=diag)."); const kid=$("kid").value.trim()||"dev-key-1";
  const hdr={alg:"EdDSA", typ:"JWT", kid}; const pld={aud:lastDiag.aud, nonce:lastDiag.nonce, ts: Math.floor(Date.now()/1000)};
  const h=b64u(enc.encode(JSON.stringify(hdr))); const p=b64u(enc.encode(JSON.stringify(pld)));
  const kp=getKeypair(); const sig=nacl.sign.detached(new TextEncoder().encode(h+"."+p), kp.secretKey);
  const jws=h+"."+p+"."+b64u(sig);
  const r=await fetch("/api/diag/echo_signed",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({jws})});
  $("diagOut").textContent=JSON.stringify(await r.json(),null,2);
}catch(e){ $("diagOut").textContent="ERR: "+e.message; } };
</script>
"""
