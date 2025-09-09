from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List

app = FastAPI(title="MeCloneMe API", version="0.1")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.get("/api/health")
def health():
    return {"ok": True, "service": "mcm_api"}

@app.post("/api/echo")
async def echo(text: str):
    return {"echo": text}

@app.post("/api/upload/audio")
async def upload_audio(file: UploadFile = File(...)):
    size = 0
    while chunk := await file.read(8192):
        size += len(chunk)
    return {"received_bytes": size, "filename": file.filename}

class Msg(BaseModel):
    text: str

STOPWORDS: List[str] = [
    "i","oraz","ale","że","to","na","w","we","o","u","z","za","do","dla","po","od",
    "jest","są","być","mam","mamy","czy","jak","co","się","że","nie","tak"
]

def extract_keywords(text: str, n: int = 3) -> List[str]:
    words = [w.strip(".,!?;:()[]\"'").lower() for w in text.split()]
    keys = [w for w in words if len(w) >= 5 and w not in STOPWORDS]
    seen, out = set(), []
    for w in keys:
        if w not in seen:
            seen.add(w); out.append(w)
        if len(out) >= n: break
    return out

@app.post("/api/reply")
def reply(msg: Msg):
    t = (msg.text or "").strip()
    keys = extract_keywords(t)
    focus = " • ".join(keys) if keys else "zrób pierwszy mikro-krok"
    reply = (
        "Jasne – działamy. "
        "Plan: 1) wybierz jeden mikro-krok, 2) zrób go w 10 min, 3) wróć z wynikiem. "
        f"Fokus: {focus}. "
        "Trzymam tempo i przypilnuję kolejnego kroku."
    )
    return {"reply": reply, "keywords": keys}