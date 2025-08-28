
from __future__ import annotations
import json, os, threading
from datetime import datetime
from typing import List, Dict, Any
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

router = APIRouter(prefix="/progress", tags=["progress"])

_LOCK = threading.Lock()
_PATH = os.environ.get("MC_PROGRESS_PATH", "/tmp/mecloneme_progress.json")

class Item(BaseModel):
    code: str = Field(pattern=r"^[A-Z]\d{2}$")
    name: str
    percent: int = Field(ge=0, le=100)
    updated_at: str

def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"

def _default_items() -> Dict[str, Any]:
    return {
        "N01": {"code":"N01","name":"SSOT / Router-README","percent":55,"updated_at":_now()},
        "N18": {"code":"N18","name":"Panel CEO","percent":35,"updated_at":_now()},
        "N22": {"code":"N22","name":"Testy & QA","percent":25,"updated_at":_now()},
        "N04": {"code":"N04","name":"Mobile (Camera/Mic)","percent":20,"updated_at":_now()},
        "N05": {"code":"N05","name":"Desktop (Bridge)","percent":20,"updated_at":_now()},
        "N09": {"code":"N09","name":"Guardian","percent":30,"updated_at":_now()},
        "N21": {"code":"N21","name":"SDK / API Clients","percent":15,"updated_at":_now()},
        "N27": {"code":"N27","name":"Docs & OpenAPI","percent":30,"updated_at":_now()},
        "N30": {"code":"N30","name":"Core (Live+AR+Guardian)","percent":40,"updated_at":_now()},
    }

def _load() -> Dict[str, Any]:
    with _LOCK:
        try:
            with open(_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and data:
                    return data
        except Exception:
            pass
        data = _default_items()
        try:
            with open(_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception:
            pass
        return data

def _save(data: Dict[str, Any]) -> None:
    with _LOCK:
        with open(_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f)

@router.get("", response_model=List[Item])
def list_items() -> List[Item]:
    data = _load()
    return [Item(**v) for k,v in sorted(data.items())]

class BulkIn(BaseModel):
    code: str
    percent: int = Field(ge=0, le=100)

@router.post("/bulk", response_model=List[Item])
def bulk_update(items: List[BulkIn]) -> List[Item]:
    data = _load()
    for it in items:
        if it.code not in data:
            data[it.code] = {"code": it.code, "name": it.code, "percent": 0, "updated_at": _now()}
        data[it.code]["percent"] = int(it.percent)
        data[it.code]["updated_at"] = _now()
    _save(data)
    return [Item(**v) for k,v in sorted(data.items())]

@router.post("/set/{code}", response_model=Item)
def set_one(code: str, percent: int) -> Item:
    data = _load()
    if code not in data:
        raise HTTPException(404, "Unknown code")
    data[code]["percent"] = int(percent)
    data[code]["updated_at"] = _now()
    _save(data)
    return Item(**data[code])

@router.get("/ui", response_class=HTMLResponse)
def ui():
    html = """
<!doctype html>
<html><head>
  <meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Postęp — MeCloneMe</title>
  <style>
    body{background:#0b0f14;color:#e5e7eb;font-family:Inter,system-ui,Segoe UI,Roboto,Helvetica,Arial,sans-serif;margin:0;padding:24px}
    h1{font-size:22px;margin:0 0 18px}
    .card{background:#0f172a;border-radius:16px;padding:18px;margin:12px 0;box-shadow:0 1px 0 #0b1220}
    .head{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
    .code{opacity:.7;font-weight:600;margin-right:6px}
    .name{font-weight:600}
    .bar{height:14px;background:#1f2937;border-radius:999px;overflow:hidden}
    .fill{height:100%;background:#22c55e;border-radius:999px;transition:width .35s ease}
    .row{display:grid;grid-template-columns:1fr 120px;gap:16px;align-items:center}
    .pct{font-weight:700;text-align:right}
    .top{max-width:860px;margin:auto}
    a{color:#93c5fd}
    input{width:100%;background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:10px;padding:8px 10px}
    button{background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:10px;padding:8px 10px;cursor:pointer}
    .admin{display:none;gap:8px}
  </style>
</head>
<body>
  <div class="top">
    <div style='display:flex;justify-content:space-between;align-items:center'>
      <h1>Postęp MeCloneMe</h1>
      <a href='/' style='text-decoration:none'><button>START</button></a>
    </div>
    <div style="margin:8px 0 16px">
      <button onclick="toggleAdmin()">✏️ Tryb edycji</button>
    </div>
    <div id="wrap"></div>
    <p style="opacity:.7;margin-top:16px">Szybkie linki: <a href="/alerts/ui">/alerts/ui</a> • <a href="/docs">/docs</a></p>
  </div>
  <script>
    let edit = false;
    function toggleAdmin(){ edit = !edit; document.querySelectorAll('.admin').forEach(x => x.style.display = edit ? 'grid' : 'none'); }
    async function load(){
      const res = await fetch('/progress');
      const data = await res.json();
      const wrap = document.getElementById('wrap');
      wrap.innerHTML = '';
      for (const it of data){
        const card = document.createElement('div'); card.className='card';
        card.innerHTML = `
          <div class="row">
            <div>
              <div class="head"><div><span class="code">${it.code}</span><span class="name">${it.name}</span></div><div class="pct" id="pct-${it.code}">${it.percent}%</div></div>
              <div class="bar"><div class="fill" id="fill-${it.code}" style="width:${it.percent}%"></div></div>
            </div>
            <div class="admin">
              <input id="inp-${it.code}" type="number" min="0" max="100" value="${it.percent}" />
              <button onclick="saveOne('${it.code}')">Zapisz</button>
            </div>
          </div>`;
        wrap.appendChild(card);
      }
    }
    async function saveOne(code){
      const v = Number(document.getElementById('inp-'+code).value||0);
      await fetch('/progress/set/'+code+'?percent='+v, {method:'POST'});
      document.getElementById('pct-'+code).textContent = v + '%';
      document.getElementById('fill-'+code).style.width = v + '%';
    }
    load();
  </script>
</body></html>
"""
    return HTMLResponse(html)
