
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from typing import Dict, Any
from datetime import datetime

app = FastAPI(title="MeCloneMe API", version="0.3.10")

app.mount("/static", StaticFiles(directory=str(__file__).replace("app.py","static")), name="static")
templates = Jinja2Templates(directory=str(__file__).replace("app.py","templates"))

@app.get("/", response_class=HTMLResponse)
@app.get("/start", response_class=HTMLResponse)
async def start(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "title":"MeCloneMe — API"})

BARS=[
    {"code":"N01","title":"SSOT / Router-README","pct":55},
    {"code":"N04","title":"Mobile (Camera/Mic)","pct":20},
    {"code":"N05","title":"Desktop (Bridge)","pct":20},
    {"code":"N09","title":"Guardian","pct":30},
    {"code":"N18","title":"Panel CEO","pct":35},
    {"code":"N21","title":"SDK / API Clients","pct":15},
    {"code":"N22","title":"Testy & QA","pct":25},
    {"code":"N27","title":"Docs & OpenAPI","pct":30},
    {"code":"N30","title":"Core (Live+AR+Guardian)","pct":40},
]
@app.get("/progress", response_class=HTMLResponse)
async def progress_page(request: Request):
    return templates.TemplateResponse("progress.html", {"request": request, "bars": BARS, "title":"Postęp — MeCloneMe"})

@app.get("/alerts/health")
async def alerts_health(): return {"ok": True}

@app.get("/alerts")
async def alerts_feed()->Dict[str,Any]:
    now=datetime.utcnow().isoformat()+"Z"
    items=[
        {"title":"High error rate","score":88,"source":"backend","tags":["api","errors"],"updated":now},
        {"title":"New signups drop","score":72,"source":"analytics","tags":["funnel"],"updated":now},
        {"title":"Abandoned carts","score":61,"source":"checkout","tags":["shop"],"updated":now},
    ]
    return {"items":items}

@app.get("/alerts/ui", response_class=HTMLResponse)
async def alerts_ui(request: Request):
    return templates.TemplateResponse("alerts_ui.html", {"request": request, "title":"Alerts — MeCloneMe"})

@app.get("/comm/mobile", response_class=HTMLResponse)
async def comm_mobile(request: Request):
    return templates.TemplateResponse("comm_mobile.html", {"request": request, "title":"Mobile (dark)"})

LISTEN_STATE={"active":False,"since":None}
@app.post("/ingest/listen")
async def ingest_listen(payload: Dict[str,Any]):
    active=bool(payload.get("listening"))
    LISTEN_STATE["active"]=active; LISTEN_STATE["since"]=datetime.utcnow().isoformat()+"Z" if active else None
    return {"listening":LISTEN_STATE["active"],"since":LISTEN_STATE["since"]}

@app.post("/ingest/consent")
async def ingest_consent(agree_audio: bool = Form(...), agree_processing: bool = Form(...)):
    return {"ok": True, "agree_audio": agree_audio, "agree_processing": agree_processing}

@app.get("/start/*", include_in_schema=False)
@app.get("/ui/*", include_in_schema=False)
async def fallback_to_start(request: Request):
    return RedirectResponse(url="/start")
