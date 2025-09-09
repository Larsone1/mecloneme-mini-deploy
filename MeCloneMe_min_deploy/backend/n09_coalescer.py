from __future__ import annotations
import json
import os
import threading
import time
import uuid
import csv
import io
from typing import List, Dict, Any
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field
from datetime import datetime

router = APIRouter(prefix="/alerts", tags=["alerts"])

_LOCK = threading.Lock()
_PATH = os.environ.get("MC_ALERTS_PATH", "/tmp/mecloneme_alerts.json")


class Alert(BaseModel):
    id: str
    title: str
    source: str
    score: int = Field(ge=0, le=100)
    tags: List[str] = []
    status: str = "open"
    muted_until: float = 0.0
    updated_at: str


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _seed() -> Dict[str, Any]:
    now = _now()
    return {
        "a": {
            "id": "a",
            "title": "High error rate",
            "source": "backend",
            "score": 88,
            "tags": ["api", "errors"],
            "status": "open",
            "muted_until": 0,
            "updated_at": now,
        },
        "b": {
            "id": "b",
            "title": "New signups drop",
            "source": "analytics",
            "score": 72,
            "tags": ["funnel"],
            "status": "open",
            "muted_until": 0,
            "updated_at": now,
        },
        "c": {
            "id": "c",
            "title": "Abandoned carts",
            "source": "checkout",
            "score": 61,
            "tags": ["shop"],
            "status": "open",
            "muted_until": 0,
            "updated_at": now,
        },
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
        data = _seed()
        try:
            with open(_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception:
            pass
        return data


def _save(data: Dict[str, Any]):
    with _LOCK:
        with open(_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f)


@router.get("", response_model=List[Alert])
def list_alerts():
    data = _load()
    return [Alert(**v) for _, v in sorted(data.items())]


class Ingest(BaseModel):
    title: str
    source: str
    score: int = 50
    tags: List[str] = []


@router.post("/ingest", response_model=List[Alert])
def ingest(items: List[Ingest]):
    data = _load()
    for it in items:
        i = str(uuid.uuid4())[:8]
        data[i] = {
            "id": i,
            "title": it.title,
            "source": it.source,
            "score": int(it.score),
            "tags": it.tags,
            "status": "open",
            "muted_until": 0,
            "updated_at": _now(),
        }
    _save(data)
    return [Alert(**v) for _, v in sorted(data.items())]


@router.post("/{id}/resolve", response_model=Alert)
def resolve(id: str):
    data = _load()
    if id not in data:
        raise HTTPException(404, "not found")
    data[id]["status"] = "resolved"
    data[id]["updated_at"] = _now()
    _save(data)
    return Alert(**data[id])


@router.post("/{id}/mute", response_model=Alert)
def mute(id: str, minutes: int = 15):
    data = _load()
    if id not in data:
        raise HTTPException(404, "not found")
    data[id]["muted_until"] = time.time() + minutes * 60
    data[id]["updated_at"] = _now()
    _save(data)
    return Alert(**data[id])


@router.post("/seed")
def seed():
    data = _seed()
    _save(data)
    return {"ok": True, "count": len(data)}


@router.get("/export")
def export_csv():
    data = _load()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "title", "source", "score", "status", "tags", "updated_at"])
    for _, v in sorted(data.items()):
        w.writerow(
            [
                v["id"],
                v["title"],
                v["source"],
                v["score"],
                v["status"],
                "|".join(v.get("tags", [])),
                v["updated_at"],
            ]
        )
    buf.seek(0)
    return StreamingResponse(
        iter([buf.read()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=alerts.csv"},
    )


@router.get("/ui", response_class=HTMLResponse)
def ui():
    html = """
<!doctype html><html><head>
  <meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Alerts — MeCloneMe</title>
  <style>
    body{background:#0b0f14;color:#e5e7eb;font-family:Inter,system-ui,Segoe UI,Roboto,Helvetica,Arial,sans-serif;margin:0;padding:24px}
    a{color:#93c5fd}
    .wrap{max-width:1024px;margin:auto}
    table{width:100%;border-collapse:collapse}
    th,td{padding:10px;border-bottom:1px solid #1f2937}
    .pill{background:#0f172a;border:1px solid #1f2937;border-radius:10px;padding:7px 10px;display:inline-block;margin-right:6px}
    button{background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:10px;padding:6px 10px;cursor:pointer}
    .row{background:#0f172a}
    .heading{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px}
  </style>
</head><body>
  <div class="wrap">
    <div class="heading">
      <h1 style="margin:0">Alerts</h1>
      <div>
        <a href="/alerts/export" style="margin-right:8px;text-decoration:none"><button>Pobierz CSV</button></a>
        <a href="/" style="text-decoration:none"><button>START</button></a>
      </div>
    </div>
    <table>
      <thead><tr><th>Title</th><th>Score</th><th>Source</th><th>Tags</th><th>Actions</th></tr></thead>
      <tbody id="tbody"></tbody>
    </table>
  </div>
  <script>
    async function load(){
      const res = await fetch('/alerts'); const data = await res.json();
      const tb = document.getElementById('tbody'); tb.innerHTML='';
      for (const it of data){
         const tr=document.createElement('tr'); tr.className='row';
         tr.innerHTML = `<td>${it.title}</td><td>${it.score}</td><td>${it.source}</td>
           <td>${(it.tags||[]).map(t=>'<span class="pill">'+t+'</span>').join('')}</td>
           <td><button onclick="act('${it.id}','resolve')">Resolve</button>
               <button onclick="act('${it.id}','mute?minutes=15')">Mute 15m</button></td>`;
         tb.appendChild(tr);
      }
    }
    async function act(id, op){ await fetch('/alerts/'+id+'/'+op, {method:'POST'}); load(); }
    load();
  </script>
</body></html>
"""
    return HTMLResponse(html)
