from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
try:
    from backend.n09_coalescer import router as alerts_router
except Exception as e:
    alerts_router = None

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
    return "<html><body style='font-family:Inter,sans-serif;padding:24px;color:#e5e7eb;background:#0b0f14'><h2>MeCloneMe — API</h2><p>✔ Live</p><p><a href='/alerts/ui'>/alerts/ui</a> • <a href='/alerts'>/alerts</a> • <a href='/alerts/health'>/alerts/health</a> • <a href='/docs'>/docs</a></p></body></html>"

if alerts_router is not None:
    app.include_router(alerts_router)