# routes_onboarding.py — EXTENDED with /diag (safe to drop-in)
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

router = APIRouter()
templates = Jinja2Templates(directory=str((Path(__file__).parent / "templates")))

# === existing onboarding steps (kept if already present) ===
@router.get("/onboarding_voice", response_class=HTMLResponse)
def ob_voice(request: Request, cid: int | None = None):
    return templates.TemplateResponse("comm_mobile.html", {"request": request})

@router.get("/onboarding_photo", response_class=HTMLResponse)
def ob_photo(request: Request, cid: int | None = None):
    return templates.TemplateResponse("mobile.html", {"request": request})

@router.get("/onboarding_video", response_class=HTMLResponse)
def ob_video(request: Request, cid: int | None = None):
    return templates.TemplateResponse("mobile.html", {"request": request})

@router.get("/onboarding_text", response_class=HTMLResponse)
def ob_text(request: Request, cid: int | None = None):
    return templates.TemplateResponse("mobile.html", {"request": request})

@router.get("/onboarding_summary", response_class=HTMLResponse)
def ob_summary(request: Request, cid: int | None = None):
    return templates.TemplateResponse("mobile.html", {"request": request})

@router.get("/onboarding_status", response_class=HTMLResponse)
def ob_status(request: Request, cid: int | None = None):
    return templates.TemplateResponse("progress.html", {"request": request})

@router.get("/onboarding-test", response_class=HTMLResponse)
def ob_test(request: Request):
    return templates.TemplateResponse("comm_mobile.html", {"request": request})

# === NEW: /diag — server route (no static needed) ===
@router.get("/diag", response_class=HTMLResponse)
def diag(request: Request):
    html = f'''
    <!doctype html>
    <html lang="pl"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
    <title>MeCloneMe · Diag</title>
    <style>
      body{{font-family:system-ui,Segoe UI,Roboto,Helvetica,Arial,sans-serif;background:#0b0b10;color:#eae;margin:0;padding:24px;line-height:1.6}}
      a{{color:#9ad1ff;text-decoration:none}} a:hover{{text-decoration:underline}}
      .card{{max-width:720px;margin:0 auto;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.08);border-radius:14px;padding:16px 18px}}
      h1{{font-size:20px;margin:8px 0 16px}}
      ul{{margin:0;padding-left:18px}}
    </style>
    <div class="card">
      <h1>MeCloneMe · Szybka diagnostyka</h1>
      <ul>
        <li><a href="/" target="_blank">/</a></li>
        <li><a href="/start" target="_blank">/start</a></li>
        <li><a href="/comm/mobile" target="_blank">/comm/mobile</a></li>
        <li><a href="/alerts/health" target="_blank">/alerts/health</a></li>
      </ul>
      <p>Jeśli tu działa, a z paska nie — Chrome otwiera wyszukiwarkę zamiast adresu. Przytrzymaj link i wybierz „Otwórz w nowej karcie”.</p>
    </div>
    '''
    return HTMLResponse(html)
