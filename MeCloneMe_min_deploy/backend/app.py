
import os, json
from pathlib import Path
from typing import Optional, Dict, Any
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

APP_DIR = Path(__file__).parent
DATA_FILE = APP_DIR / "data" / "finanse.json"

app = FastAPI(title="MeCloneMe — API", version="0.3.12")

# Static & templates
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

# Home / Start
@app.get("/", response_class=HTMLResponse)
@app.get("/start", response_class=HTMLResponse)
def home(request: Request):
    links = [
        ("/progress", "Postęp projektu"),
        ("/alerts/ui", "Alerts UI"),
        ("/finanse", "Finanse (KPI)"),
        ("/comm/mobile", "Komunikator (mic glow)"),
        ("/docs", "OpenAPI docs")
    ]
    return templates.TemplateResponse("index.html", {"request": request, "links": links})

# Health
@app.get("/alerts/health")
def health():
    return {"ok": True}

# Alerts UI (mock)
@app.get("/alerts/ui", response_class=HTMLResponse)
def alerts_ui(request: Request):
    alerts = [
        {"title": "High error rate", "score": 88, "source": "backend", "tags": ["api","errors"]},
        {"title": "New signups drop", "score": 72, "source": "analytics", "tags": ["funnel"]},
        {"title": "Abandoned carts", "score": 61, "source": "checkout", "tags": ["shop"]},
    ]
    return templates.TemplateResponse("alerts.html", {"request": request, "alerts": alerts})

# Progress (simple visual - safe fallback)
@app.get("/progress", response_class=HTMLResponse)
def progress(request: Request):
    items = [
        ("N01 — SSOT / Router-README", 55),
        ("N04 — Mobile (Camera/Mic)", 20),
        ("N05 — Desktop (Bridge)", 20),
        ("N09 — Guardian", 30),
        ("N18 — Panel CEO", 35),
        ("N21 — SDK / API Clients", 15),
        ("N22 — Testy & QA", 25),
        ("N27 — Docs & OpenAPI", 30),
        ("N30 — Core (Live+AR+Guardian)", 40),
    ]
    return templates.TemplateResponse("progress.html", {"request": request, "items": items})

# Finance persistence helpers
def _load_finanse() -> Dict[str, Any]:
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def _save_finanse(payload: Dict[str, Any]) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

# Finance API
@app.get("/api/finanse")
def get_finanse():
    return _load_finanse()

@app.post("/api/finanse")
async def post_finanse(request: Request):
    data = await request.json()
    _save_finanse(data)
    return {"ok": True}

# Finance UI
@app.get("/finanse", response_class=HTMLResponse)
def finanse_ui(request: Request):
    initial = _load_finanse()
    return templates.TemplateResponse("finanse.html", {"request": request, "initial": initial})

# Mock endpoint for audio ingest (stub MVP)
@app.post("/ingest/listen")
async def ingest_listen():
    return {"ok": True, "note": "MVP stub — audio ingest to be implemented."}

# Communicator (mic glow)
@app.get("/comm/mobile", response_class=HTMLResponse)
def comm_mobile(request: Request):
    return templates.TemplateResponse("comm_mobile.html", {"request": request})
