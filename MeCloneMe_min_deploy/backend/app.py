
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import os, time, json, asyncio

app = FastAPI(title="MeCloneMe API (mini)")

PANEL_HTML = "<!doctype html><meta charset=\"utf-8\"><title>Guardian Mini Panel</title>\n<body style=\"font-family: -apple-system, system-ui, sans-serif; padding:16px\">\n  <h1>Guardian \u2014 mini panel</h1>\n  <div style=\"display:grid;grid-template-columns:1fr 1fr;gap:16px\">\n    <div style=\"border:1px solid #eee;border-radius:12px;padding:12px\">\n      <h2 style=\"margin:0 0 8px\">Challenge</h2>\n      <pre id=\"challenge\" style=\"background:#f7f7f7;padding:12px;border-radius:8px\">...</pre>\n    </div>\n    <div style=\"border:1px solid #eee;border-radius:12px;padding:12px\">\n      <h2 style=\"margin:0 0 8px\">Live log</h2>\n      <pre id=\"log\" style=\"background:#f7f7f7;padding:12px;border-radius:8px;height:260px;overflow:auto\"></pre>\n    </div>\n  </div>\n  <script>\n    fetch('/auth/challenge').then(r=>r.json()).then(x=>{\n      document.getElementById('challenge').textContent = JSON.stringify(x,null,2);\n    }).catch(()=>{document.getElementById('challenge').textContent='API offline';});\n    const log = document.getElementById('log');\n    try{\n      const ws = new WebSocket((location.protocol==='https:'?'wss':'ws')+'://'+location.host+'/shadow/ws');\n      ws.onmessage = e => { try{\n        const m = JSON.parse(e.data);\n        if(m.vec){ log.textContent += JSON.stringify(m)+'\\n'; log.scrollTop = log.scrollHeight; }\n      }catch{} };\n    }catch(e){ log.textContent = 'WS error: '+e; }\n  </script>\n</body>"

@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(PANEL_HTML)

class ChallengeResp(BaseModel):
    nonce: str
    aud: str
    ts: int

@app.get("/auth/challenge", response_model=ChallengeResp)
def challenge(aud: str = "mobile"):
    return {"nonce": os.urandom(16).hex(), "aud": aud, "ts": int(time.time())}

class VerifyReq(BaseModel):
    jws: str

@app.post("/guardian/verify")
def guardian_verify(req: VerifyReq):
    return {"ok": True} if len(req.jws.split(".")) == 3 else {"ok": False, "reason": "bad-jws"}

class WSManager:
    def __init__(self):
        self.active = []
        self.lock = asyncio.Lock()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        async with self.lock:
            self.active.append(ws)

    async def disconnect(self, ws: WebSocket):
        async with self.lock:
            if ws in self.active:
                self.active.remove(ws)

    async def broadcast(self, data: dict):
        text = json.dumps(data)
        dead = []
        for ws in list(self.active):
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(ws)

ws_manager = WSManager()

@app.websocket("/shadow/ws")
async def shadow_ws(ws: WebSocket):
    await ws_manager.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        await ws_manager.disconnect(ws)

class ShadowFrame(BaseModel):
    ts: int
    kid: str | None = None
    vec: dict = {}

@app.post("/shadow/ingest")
async def shadow_ingest(frame: ShadowFrame):
    os.makedirs("logs", exist_ok=True)
    with open("logs/shadow.jsonl", "a") as f:
        f.write(json.dumps(frame.dict()) + "\n")
    await ws_manager.broadcast(frame.dict())
    return {"ok": True}
