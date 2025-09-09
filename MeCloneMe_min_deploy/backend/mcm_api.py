from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Dict, Any
from pathlib import Path
from uuid import uuid4
import yaml, os

app = FastAPI(title="MeCloneMe API", version="0.3")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ---------- Paths / Persona ----------
ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "clone" / "profile.yml"
DATA_DIR = ROOT / "data"
(DATA_DIR).mkdir(parents=True, exist_ok=True)

def load_profile() -> Dict[str, Any]:
    if PROFILE_PATH.exists():
        with open(PROFILE_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}

PROFILE = load_profile()

@app.get("/api/persona")
def persona():
    return PROFILE or {"name": "Superclone", "persona": {"role": "Asystent", "tone": "konkretny"}}

# serwowanie plików
app.mount("/files", StaticFiles(directory=str(DATA_DIR)), name="files")

# ---------- Utils ----------
STOPWORDS: List[str] = ["i","oraz","ale","że","to","na","w","we","o","u","z","za","do","dla","po","od",
                        "jest","są","być","mam","mamy","czy","jak","co","się","nie","tak"]

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

class Msg(BaseModel):
    text: str

@app.post("/api/reply")
def reply(msg: Msg):
    return {"reply": generate_reply(msg.text), "keywords": extract_keywords(msg.text or "")}

# sesja
@app.post("/api/session/new")
def new_session():
    sid = uuid4().hex
    (DATA_DIR / sid / "audio").mkdir(parents=True, exist_ok=True)
    (DATA_DIR / sid / "image").mkdir(parents=True, exist_ok=True)
    return {"sid": sid}

# uploady
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