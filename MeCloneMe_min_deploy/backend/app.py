from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path
import time
from starlette.middleware.cors import CORSMiddleware

app = FastAPI(title="MeCloneMe")

# --- CORS for GH Pages ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=['https://larsone1.github.io'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)
# -------------------------
BASE = Path(__file__).parent

# statics
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))

# VERSION for cache busting
templates.env.globals["version"] = str(int(time.time()))


# health
@app.get("/alerts/health")
def health():
    return JSONResponse({"ok": True})


# root SW + manifest (ważne dla PWA)
@app.get("/sw.js")
def sw_root():
    return FileResponse(
        BASE / "static" / "js" / "sw.js", media_type="application/javascript"
    )


@app.get("/manifest.webmanifest")
def manifest_root():
    return FileResponse(
        BASE / "static" / "manifest.webmanifest", media_type="application/manifest+json"
    )


# pages
@app.get("/", response_class=HTMLResponse)
def splash(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/pwa", response_class=HTMLResponse)
def pwa_page(request: Request):
    return templates.TemplateResponse("pwa_boot.html", {"request": request})


@app.get("/onboarding", response_class=HTMLResponse)
def onboarding(request: Request):
    return templates.TemplateResponse("onboarding_mobile.html", {"request": request})


@app.get("/start", response_class=HTMLResponse)
def start(request: Request):
    return templates.TemplateResponse("start.html", {"request": request})


@app.get("/mobile", response_class=HTMLResponse)
def mobile(request: Request):
    return templates.TemplateResponse("mobile.html", {"request": request})


@app.get("/comm/mobile", response_class=HTMLResponse)
def comm_mobile(request: Request):
    return templates.TemplateResponse("comm_mobile.html", {"request": request})


@app.get("/marketing", response_class=HTMLResponse)
def marketing(request: Request):
    return templates.TemplateResponse("marketing.html", {"request": request})


@app.get("/finanse", response_class=HTMLResponse)
def finanse(request: Request):
    return templates.TemplateResponse("finanse.html", {"request": request})


@app.get("/progress", response_class=HTMLResponse)
def progress(request: Request):
    return templates.TemplateResponse("progress.html", {"request": request})
