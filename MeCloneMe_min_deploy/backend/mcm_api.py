from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware

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
