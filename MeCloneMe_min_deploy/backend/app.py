
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

def _safe_import(path, name):
    try:
        mod = __import__(path, fromlist=[name])
        return getattr(mod, name)
    except Exception:
        return None

alerts_router   = _safe_import("backend.n09_coalescer", "router")
progress_router = _safe_import("backend.n27_progress", "router")
tasks_router    = _safe_import("backend.n28_tasks", "router")
ai_router       = _safe_import("backend.n10_ai_roster", "router")

app = FastAPI(title="MeCloneMe API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/auth/challenge")
def _render_health():
    return {"ok": True}

@app.get("/alerts/health")
def _alerts_health():
    return {"ok": True}

@app.get("/", response_class=HTMLResponse)
def _root():
    html = """
<!doctype html><html><head>
  <meta charset='utf-8'/><meta name='viewport' content='width=device-width,initial-scale=1'/>
  <title>MeCloneMe — API</title>
  <style>
    body{background:#0b0f14;color:#e5e7eb;font-family:Inter,system-ui,Segoe UI,Roboto,Helvetica,Arial,sans-serif;margin:0;padding:24px}
    a{color:#93c5fd;text-decoration:none}
    .wrap{max-width:900px;margin:auto}
    .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin-top:12px}
    .card{background:#0f172a;border:1px solid #1f2937;border-radius:14px;padding:14px}
    .h{margin:0 0 10px}
  </style>
</head><body>
  <div class="wrap">
    <h2 class="h">MeCloneMe — API</h2>
    <p>✔ Live</p>
    <div class="grid">
      <div class="card"><b>Alerts</b><br><a href="/alerts/ui">/alerts/ui</a></div>
      <div class="card"><b>Postęp</b><br><a href="/progress/ui">/progress/ui</a></div>
      <div class="card"><b>Zadania (mini‑Gantt)</b><br><a href="/tasks/ui">/tasks/ui</a></div>
      <div class="card"><b>AI Roster</b><br><a href="/ai/ui">/ai/ui</a></div>
      <div class="card"><b>OpenAPI</b><br><a href="/docs">/docs</a></div>
    </div>
  </div>
</body></html>
"""
    return HTMLResponse(html)

if alerts_router:   app.include_router(alerts_router)
if progress_router: app.include_router(progress_router)
if tasks_router:    app.include_router(tasks_router)
if ai_router:       app.include_router(ai_router)
