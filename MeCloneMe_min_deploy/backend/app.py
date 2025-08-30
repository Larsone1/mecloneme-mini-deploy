from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path

app = FastAPI(title="MeCloneMe")
BASE = Path(__file__).parent
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))

# --- Health (Render) ---
@app.get("/alerts/health")
def health():
    return JSONResponse({"ok": True})

def tpl(name: str, request: Request):
    return templates.TemplateResponse(name, {"request": request})

# --- Standard pages ---
@app.get("/", response_class=HTMLResponse)
def root(request: Request):           return tpl("index.html", request)

@app.get("/start", response_class=HTMLResponse)
def start(request: Request):          return tpl("start.html", request)

@app.get("/mobile", response_class=HTMLResponse)
def mobile(request: Request):         return tpl("mobile.html", request)

@app.get("/comm/mobile", response_class=HTMLResponse)
def comm_mobile(request: Request):    return tpl("comm_mobile.html", request)

@app.get("/marketing", response_class=HTMLResponse)
def marketing(request: Request):      return tpl("marketing.html", request)

@app.get("/finanse", response_class=HTMLResponse)
def finanse(request: Request):        return tpl("finanse.html", request)

@app.get("/progress", response_class=HTMLResponse)
def progress(request: Request):       return tpl("progress.html", request)

# --- Onboarding (inline) ---
@app.get("/onboarding", response_class=HTMLResponse)
def onboarding(request: Request):
    # użyjemy gotowego szablonu z templates/
    return tpl("onboarding_mobile.html", request)

# --- Optional: include router if present (routes_onboarding.py) ---
try:
    from .routes_onboarding import router as onboarding_router  # type: ignore
    app.include_router(onboarding_router)
except Exception:
    pass
