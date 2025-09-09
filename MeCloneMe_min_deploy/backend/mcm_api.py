from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Dict, Any
from pathlib import Path
from uuid import uuid4
import yaml, json, os

VERSION = "0.4.2"

# ---------- CORS (konfig z env) ----------
_origins = os.environ.get("ALLOWED_ORIGINS", "*").strip()
if _origins == "*":
    ALLOW_ORIGINS = ["*"]; ALLOW_CREDS = False
elif _origins == "":
    ALLOW_ORIGINS = []; ALLOW_CREDS = False
else:
    ALLOW_ORIGINS = [o.strip() for o in _origins.split(",") if o.strip()]
    ALLOW_CREDS = True

app = FastAPI(title="MeCloneMe API", version=VERSION)
app.add_middleware(CORSMiddleware,
                   allow_origins=ALLOW_ORIGINS,
                   allow_methods=["*"],
                   allow_headers=["*"],
                   allow_credentials=ALLOW_CREDS)

# ---------- Paths / Persona ----------
ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "clone" / "profile.yml"
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

def load_profile() -> Dict[str, Any]:
    if PROFILE_PATH.exists():
        with open(PROFILE_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}

PROFILE = load_profile()
app.mount("/files", StaticFiles(directory=str(DATA_DIR)), name="files")

# ---------- Sesje: RAM + dysk ----------
SESS: Dict[str, Dict[str, Any]] = {}  # sid -> {"history": [...]} 

def hist_path(sid: str) -> Path:
    return DATA_DIR / sid / "history.json"

def get_hist(sid: str) -> List[Dict[str, str]:
    if sid not in SESS:
        # spróbuj doczytać z dysku
        hp = hist_path(sid)
        if hp.exists():
            try:
                SESS[sid] = {"history": json.loads(hp.read_text(encoding="utf-8"))}
            except Exception:
                SESS[sid] = {"history": []}
        else:
            SESS[sid] = {"history": []}
    return SESS[sid]["history"]

def save_hist(sid: str) -> None:
    hp = hist_path(sid)
    hp.parent.mkdir(parents=True, exist_ok=True)
    hp.write_text(json.dumps(SESS[sid]["history"], ensure_ascii=False, indent=2), encoding="utf-8")

# ---------- Utils ----------
STOPWORDS: List[str] = ["i","oraz","ale","że","to","na","w","we","o","u","z","za","do","dla","po","od","jest","są","być","mam","mamy","czy","jak","co","się","nie","tak"]

def extract_keywords(text: str, n: int = 3) -> List[str]:
    words = [w.strip(".,!?;:()[]\"'").lower() for w in (text or "").split()]
    keys = [w for w in words if len(w) >= 5 and w not in STOPWORDS]
    seen, out = set(), []
    for w in keys:
        if w not in seen:
            seen.add(w); out.append(w)
        if len(out) >= n: break
    return out

def style_compact(sentences: List[str]) -> str:
    out = []
    for s in sentences:
        s = s.strip()
        if not s: continue
        parts = s.split()
        while len(parts) > 14:
            out.append(" ".join(parts[:14]).rstrip(",.;:") + ".")
            parts = parts[14:]
        out.append(" ".join(parts).rstrip(",.;:") + ".")
    return " ".join(out)

def generate_reply(user_text: str) -> str:
    role = PROFILE.get("persona", {}).get("role", "Asystent")
    tone = PROFILE.get("persona", {}).get("tone", "konkretny")
    keys = extract_keywords(user_text)
    fokus = " • ".join(keys) if keys else "pierwszy mikro-krok"
    raw = [f"{role}. Ton: {tone}.", "Plan: 1) wybierz mikro-krok, 2) 10 min, 3) wróć z wynikiem", f"Fokus: {fokus}", "Jestem przy Tobie — działamy"]
    return style_compact(raw)

# ---------- API ----------
@app.get("/api/health")
def health():
    return {"ok": True, "service": "mcm_api", "profile": (PROFILE.get("name") or "Superclone")}

@app.get("/api/version")
def version():
    return {"version": VERSION, "service": "mcm_api"}

# Sesje
@app.post("/api/session/new")
def new_session():
    sid = uuid4().hex
    (DATA_DIR / sid / "audio").mkdir(parents=True, exist_ok=True)
    (DATA_DIR / sid / "image").mkdir(parents=True, exist_ok=True)
    SESS[sid] = {"history": []}
    save_hist(sid)
    return {"sid": sid}

@app.post("/api/session/reset")
def reset_session(sid: str = Form(...)):
    SESS[sid] = {"history": []}
    save_hist(sid)
    return {"ok": True}

@app.post("/api/session/soft-reset")
def soft_reset(sid: str = Form(...)):
    SESS.setdefault(sid, {"history": []})["history"] = []
    save_hist(sid)
    return {"ok": True}

@app.get("/api/session/{sid}/history")
def get_history(sid: str):
    return {"sid": sid, "history": get_hist(sid)}

# Chat
class Msg(BaseModel):
    sid: str
    text: str

@app.post("/api/chat/send")
def chat_send(msg: Msg):
    if not msg.sid:
        raise HTTPException(400, "sid required")
    hist = get_hist(msg.sid)
    hist.append({"who": "user", "text": msg.text})
    bot = generate_reply(msg.text)
    hist.append({"who": "bot", "text": bot})
    save_hist(msg.sid)
    return {"reply": bot, "history": hist}

# Uploady + listing
@app.post("/api/upload/audio")
async def upload_audio(file: UploadFile = File(...), sid: str = Form(None)):
    if not sid: raise HTTPException(400, "sid required")
    target_dir = DATA_DIR / sid / "audio"; target_dir.mkdir(parents=True, exist_ok=True)
    fname = file.filename or f"audio-{uuid4().hex}.webm"
    path = target_dir / fname
    with path.open("wb") as f:
        while chunk := await file.read(8192):
            f.write(chunk)
    return {"ok": True, "filename": fname, "bytes": path.stat().st_size, "url": f"/files/{sid}/audio/{fname}"}

@app.post("/api/upload/image")
async def upload_image(file: UploadFile = File(...), sid: str = Form(None)):
    if not sid: raise HTTPException(400, "sid required")
    target_dir = DATA_DIR / sid / "image"; target_dir.mkdir(parents=True, exist_ok=True)
    fname = file.filename or f"image-{uuid4().hex}.png"
    path = target_dir / fname
    with path.open("wb") as f:
        while chunk := await file.read(8192):
            f.write(chunk)
    return {"ok": True, "filename": fname, "bytes": path.stat().st_size, "url": f"/files/{sid}/image/{fname}"}

@app.get("/api/files")
def list_files(sid: str):
    base = DATA_DIR / sid
    out = {"audio": [], "image": []}
    for kind in out.keys():
        d = base / kind
        if d.exists():
            for p in sorted(d.iterdir()):
                out[kind].append({"name": p.name, "bytes": p.stat().st_size, "url": f"/files/{sid}/{kind}/{p.name}"})
    return out

# Eksport historii
@app.get("/api/session/export")
def export_history(sid: str):
    hp = hist_path(sid)
    if not hp.exists():
        raise HTTPException(404, "no history")
    return FileResponse(path=hp, media_type="application/json", filename=f"{sid}-history.json")

# Echo
@app.post("/api/echo")
async def echo(text: str):
    return {"echo": text}