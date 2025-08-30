from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
import json, os, shutil, time

router = APIRouter()
BASE = Path(__file__).parent
templates = Jinja2Templates(directory=str((BASE / "templates")))
DATA_DIR = BASE / "data"; USER_DIR = BASE / "static" / "userdata"
DATA_DIR.mkdir(exist_ok=True); USER_DIR.mkdir(exist_ok=True)
INDEX = DATA_DIR / "index.json"

def _load():
    if INDEX.exists():
        try: return json.loads(INDEX.read_text())
        except: return {}
    return {}

def _save(d): INDEX.write_text(json.dumps(d, ensure_ascii=False, indent=2))

@router.get("/onboarding", response_class=HTMLResponse)
def onboarding_page(request: Request):
    return templates.TemplateResponse("onboarding_mobile.html", {"request": request})

@router.post("/api/clone/start")
async def api_start(payload: dict):
    d=_load(); sid=payload.get("sid") or str(int(time.time()))
    d.setdefault(sid, {"consents":payload.get("consents",{}),"email":payload.get("email"),"dob":payload.get("dob"),"ref":payload.get("ref"),
                       "voice":[], "photos":[], "video":None, "texts":[], "status":{"progress":5,"message":"Zainicjowano"}})
    _save(d); return {"ok":True,"sid":sid}

@router.post("/api/clone/consent")
async def api_consent(payload: dict):
    d=_load(); sid=payload.get("sid")
    if sid in d: d[sid]["consents"]=payload.get("consents", d[sid].get("consents",{})); _save(d)
    return {"ok":True}

@router.post("/api/clone/voice")
async def api_voice(sid: str = Form(...), file: UploadFile = File(...), idx: str | None = Form(None)):
    (USER_DIR / sid).mkdir(parents=True, exist_ok=True)
    dest = USER_DIR / sid / (file.filename or f"voice_{int(time.time())}.webm")
    with dest.open("wb") as f: shutil.copyfileobj(file.file, f)
    d=_load(); rec=d.setdefault(sid,{}); rec.setdefault("voice",[]).append(dest.name); rec.setdefault("status",{"progress":0,"message":""})
    v=len(rec["voice"]); rec["status"]["progress"]=min(30, v*10); rec["status"]["message"]=f"Zebrano {v} próbek głosu"; _save(d)
    return {"ok":True,"stored":dest.name,"progress":rec["status"]["progress"]}

@router.post("/api/clone/photo")
async def api_photo(sid: str = Form(...), file: UploadFile = File(...), type: str | None = Form("photo")):
    (USER_DIR / sid).mkdir(parents=True, exist_ok=True)
    dest = USER_DIR / sid / (file.filename or f"photo_{int(time.time())}.jpg")
    with dest.open("wb") as f: shutil.copyfileobj(file.file, f)
    d=_load(); rec=d.setdefault(sid,{}); rec.setdefault("photos",[]).append(dest.name); rec.setdefault("status",{"progress":0,"message":""})
    p=len(rec["photos"]); rec["status"]["progress"]=max(rec["status"]["progress"], min(60, 30+p*5)); rec["status"]["message"]=f"Zdjęcia: {p}"; _save(d)
    return {"ok":True,"stored":dest.name,"progress":rec["status"]["progress"]}

@router.post("/api/clone/video")
async def api_video(sid: str = Form(...), file: UploadFile = File(...), type: str | None = Form("video")):
    (USER_DIR / sid).mkdir(parents=True, exist_ok=True)
    dest = USER_DIR / sid / (file.filename or f"video_{int(time.time())}.mp4")
    with dest.open("wb") as f: shutil.copyfileobj(file.file, f)
    d=_load(); rec=d.setdefault(sid,{}); rec["video"]=dest.name; rec.setdefault("status",{"progress":0,"message":""})
    rec["status"]["progress"]=max(rec["status"]["progress"],65); rec["status"]["message"]="Wideo zapisane"; _save(d)
    return {"ok":True,"stored":dest.name,"progress":rec["status"]["progress"]}

@router.post("/api/clone/text")
async def api_text(payload: dict):
    d=_load(); sid=payload.get("sid")
    if sid:
        rec=d.setdefault(sid,{}); rec.setdefault("texts",[]).extend(payload.get("samples",[]))
        rec.setdefault("status",{"progress":0,"message":""})
        rec["status"]["progress"]=max(rec["status"]["progress"],80); rec["status"]["message"]="Styl pisania zapisany"
        _save(d)
    return {"ok":True}

@router.post("/api/clone/train")
async def api_train(payload: dict):
    d=_load(); sid=payload.get("sid")
    if sid:
        rec=d.setdefault(sid,{}); rec.setdefault("status",{"progress":0,"message":""})
        rec["status"]["progress"]=max(rec["status"]["progress"],85); rec["status"]["message"]="Trening uruchomiony (MVP mock)"
        _save(d)
    return {"ok":True}

@router.get("/api/clone/status")
async def api_status(sid: str):
    d=_load(); rec=d.get(sid,{}); st=rec.get("status",{"progress":0,"message":""})
    p=st.get("progress",0)
    if p<100:
        p=min(100,p+7); st["progress"]=p
        if p>=100: st["message"]="Klon gotowy (demo)"
        d[sid]["status"]=st; _save(d)
    return st

@router.get("/api/clone/profile")
async def api_profile(sid: str):
    d=_load(); return JSONResponse(d.get(sid,{}))
