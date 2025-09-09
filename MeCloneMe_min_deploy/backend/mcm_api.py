from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any
from pathlib import Path
import yaml

app = FastAPI(title="MeCloneMe API", version="0.2")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ---------- Persona / Profile ----------
ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "clone" / "profile.yml"

DEFAULT_PROFILE: Dict[str, Any] = {
    "name": "Superclone",
    "persona": {"role": "Asystent-mentor", "tone": "ciepły, konkretny, motywujący", "style_rules": []},
}

def load_profile() -> Dict[str, Any]:
    if PROFILE_PATH.exists():
        with open(PROFILE_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or DEFAULT_PROFILE
    return DEFAULT_PROFILE

PROFILE = load_profile()

@app.get("/api/persona")
def persona():
    return PROFILE

# ---------- Utils ----------
STOPWORDS: List[str] = [
    "i","oraz","ale","że","to","na","w","we","o","u","z","za","do","dla","po","od",
    "jest","są","być","mam","mamy","czy","jak","co","się","nie","tak"
]

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
        if not s:
            continue
        # maks. ~14 słów na zdanie – cięcie „mentorskie”
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
    # szkic w stylu: krótko, klarownie, decyzja
    raw = [
        f"{role}. Ton: {tone}.",
        "Plan: 1) wybierz jeden mikro-krok, 2) zrób go w 10 min, 3) wróć z wynikiem",
        f"Fokus: {fokus}",
        "Jestem przy Tobie — działamy"
    ]
    return style_compact(raw)

# ---------- API ----------
@app.get("/api/health")
def health():
    return {"ok": True, "service": "mcm_api", "profile": PROFILE.get("name", "Superclone")}

class Msg(BaseModel):
    text: str

@app.post("/api/reply")
def reply(msg: Msg):
    return {"reply": generate_reply(msg.text), "keywords": extract_keywords(msg.text or "")}

@app.post("/api/echo")
async def echo(text: str):
    return {"echo": text}

@app.post("/api/upload/audio")
async def upload_audio(file: UploadFile = File(...)):
    size = 0
    while chunk := await file.read(8192):
        size += len(chunk)
    return {"received_bytes": size, "filename": file.filename}
