
import os, json
from typing import Dict, Any, List
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

APP_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(APP_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

app = FastAPI(title="MeCloneMe API")

app.mount("/static", StaticFiles(directory=os.path.join(APP_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(APP_DIR, "templates"))

def _read_json(name:str)->Dict[str,Any]:
    path=os.path.join(DATA_DIR, name)
    if not os.path.exists(path): return {}
    try:
        with open(path,"r",encoding="utf-8") as f: return json.load(f)
    except Exception: return {}

def _write_json(name:str, data:Dict[str,Any]):
    path=os.path.join(DATA_DIR, name)
    with open(path,"w",encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)

@app.get("/", response_class=HTMLResponse)
def home(request:Request):
    return templates.TemplateResponse("index.html", {"request":request, "title":"MeCloneMe"})

@app.get("/start", response_class=HTMLResponse)
def start(request:Request):
    return templates.TemplateResponse("start.html", {"request":request, "title":"Start"})

@app.get("/alerts/health")
def health():
    return {"ok": True}

@app.get("/alerts/ui", response_class=HTMLResponse)
def alerts_ui(request:Request):
    alerts=[
        {"title":"High error rate","source":"backend","score":88,"tags":["api","errors"]},
        {"title":"New signups drop","source":"analytics","score":72,"tags":["funnel"]},
        {"title":"Abandoned carts","source":"checkout","score":61,"tags":["shop"]},
    ]
    return templates.TemplateResponse("alerts.html", {"request":request, "alerts":alerts, "title":"Alerts"})

@app.get("/progress", response_class=HTMLResponse)
def progress(request:Request):
    progress=[
        {"code":"N01","name":"SSOT / Router-README","pct":55},
        {"code":"N04","name":"Mobile (Camera/Mic)","pct":20},
        {"code":"N05","name":"Desktop (Bridge)","pct":20},
        {"code":"N09","name":"Guardian","pct":30},
        {"code":"N18","name":"Panel CEO","pct":35},
        {"code":"N21","name":"SDK / API Clients","pct":15},
        {"code":"N22","name":"Testy & QA","pct":25},
        {"code":"N27","name":"Docs & OpenAPI","pct":30},
        {"code":"N30","name":"Core (Live+AR+Guardian)","pct":40},
    ]
    return templates.TemplateResponse("progress.html", {"request":request, "progress":progress, "title":"Postęp"})

@app.get("/finanse", response_class=HTMLResponse)
def finanse_page(request:Request):
    data=_read_json("finanse.json")
    return templates.TemplateResponse("finanse.html", {"request":request, "data":data, "title":"Finanse"})

@app.post("/api/finanse")
async def finanse_save(payload:Dict[str,Any]):
    _write_json("finanse.json", payload)
    return {"ok":True}

@app.get("/api/finanse")
def finanse_get():
    return _read_json("finanse.json")

@app.get("/marketing", response_class=HTMLResponse)
def marketing_page(request:Request):
    data=_read_json("marketing.json")
    return templates.TemplateResponse("marketing.html", {"request":request, "data":data, "title":"Marketing"})

@app.post("/api/marketing")
async def marketing_save(payload:Dict[str,Any]):
    _write_json("marketing.json", payload)
    return {"ok":True}

@app.get("/api/marketing")
def marketing_get():
    return _read_json("marketing.json")

@app.get("/comm/mobile", response_class=HTMLResponse)
def comm_mobile(request:Request):
    return templates.TemplateResponse("comm_mobile.html", {"request":request, "title":"Komunikator"})

@app.post("/ingest/listen")
async def ingest_listen():
    # MVP/stub: tutaj docelowo wejście audio i przetwarzanie
    return {"accepted": True, "note":"MVP stub"}
