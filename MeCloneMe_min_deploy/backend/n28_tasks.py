from __future__ import annotations
import json
import os
import threading
import uuid
import csv
import io
from datetime import datetime, timedelta, date
from typing import List, Dict, Any
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

router = APIRouter(prefix="/tasks", tags=["tasks"])

_LOCK = threading.Lock()
_PATH = os.environ.get("MC_TASKS_PATH", "/tmp/mecloneme_tasks.json")


class Task(BaseModel):
    id: str
    title: str
    owner: str = "CEO"
    assignee: str = "AI"
    status: str = Field("todo", pattern="^(todo|in_progress|blocked|done)$")
    due: str  # ISO date
    progress: int = Field(0, ge=0, le=100)
    updated_at: str


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _today(n=0) -> str:

    return (date.today() + timedelta(days=n)).isoformat()


def _seed() -> Dict[str, Any]:
    return {
        "t1": {
            "id": "t1",
            "title": "Start w Brazylii (jurysdykcja)",
            "owner": "CEO",
            "assignee": "Prawnik AI",
            "status": "in_progress",
            "due": _today(7),
            "progress": 35,
            "updated_at": _now(),
        },
        "t2": {
            "id": "t2",
            "title": "Kampania launch (IG/TikTok)",
            "owner": "CEO",
            "assignee": "Marketing AI",
            "status": "todo",
            "due": _today(10),
            "progress": 10,
            "updated_at": _now(),
        },
        "t3": {
            "id": "t3",
            "title": "Integracja płatności Pix",
            "owner": "CTO",
            "assignee": "Dev AI",
            "status": "blocked",
            "due": _today(14),
            "progress": 20,
            "updated_at": _now(),
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


def _save(data: Dict[str, Any]) -> None:
    with _LOCK:
        with open(_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f)


@router.get("", response_model=List[Task])
def list_tasks() -> List[Task]:
    data = _load()
    return [Task(**v) for _, v in sorted(data.items())]


class TaskIn(BaseModel):
    title: str
    owner: str = "CEO"
    assignee: str = "AI"
    status: str = "todo"
    due: str = _today(7)
    progress: int = 0


@router.post("", response_model=Task)
def create_task(it: TaskIn) -> Task:
    data = _load()
    i = str(uuid.uuid4())[:8]
    data[i] = {
        "id": i,
        "title": it.title,
        "owner": it.owner,
        "assignee": it.assignee,
        "status": it.status,
        "due": it.due,
        "progress": int(it.progress),
        "updated_at": _now(),
    }
    _save(data)
    return Task(**data[i])


@router.post("/{id}/status", response_model=Task)
def set_status(id: str, value: str) -> Task:
    data = _load()
    if id not in data:
        raise HTTPException(404, "not found")
    data[id]["status"] = value
    data[id]["updated_at"] = _now()
    _save(data)
    return Task(**data[id])


@router.post("/{id}/progress", response_model=Task)
def set_progress(id: str, value: int) -> Task:
    data = _load()
    if id not in data:
        raise HTTPException(404, "not found")
    data[id]["progress"] = int(value)
    data[id]["updated_at"] = _now()
    _save(data)
    return Task(**data[id])


@router.get("/export")
def export_csv():
    data = _load()
    buf = io.StringIO()
    wr = csv.writer(buf)
    wr.writerow(
        ["id", "title", "owner", "assignee", "status", "due", "progress", "updated_at"]
    )
    for _, v in sorted(data.items()):
        wr.writerow(
            [
                v["id"],
                v["title"],
                v["owner"],
                v["assignee"],
                v["status"],
                v["due"],
                v["progress"],
                v["updated_at"],
            ]
        )
    buf.seek(0)
    return StreamingResponse(
        iter([buf.read()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=tasks.csv"},
    )


@router.get("/ui", response_class=HTMLResponse)
def ui():
    html = """
<!doctype html><html><head>
  <meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Zadania — MeCloneMe</title>
  <style>
    body{background:#0b0f14;color:#e5e7eb;font-family:Inter,system-ui,Segoe UI,Roboto,Helvetica,Arial,sans-serif;margin:0;padding:24px}
    a{color:#93c5fd}
    .wrap{max-width:1080px;margin:auto}
    .heading{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px}
    table{width:100%;border-collapse:collapse}
    th,td{padding:10px;border-bottom:1px solid #1f2937}
    .row{background:#0f172a}
    .bar{height:10px;background:#1f2937;border-radius:999px;overflow:hidden}
    .fill{height:100%;background:#22c55e;border-radius:999px}
    select,button,input{background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:10px;padding:6px 10px}
    .due{opacity:.8}
  </style>
</head><body>
  <div class="wrap">
    <div class="heading">
      <h1 style="margin:0">Zadania</h1>
      <div>
        <a href="/tasks/export" style="margin-right:8px;text-decoration:none"><button>Pobierz CSV</button></a>
        <a href="/" style="text-decoration:none"><button>START</button></a>
      </div>
    </div>
    <table>
      <thead><tr><th>Tytuł</th><th>Owner</th><th>Assignee</th><th>Status</th><th>Due</th><th>Postęp</th><th>Akcje</th></tr></thead>
      <tbody id="tb"></tbody>
    </table>
  </div>
  <script>
    const STATUS = ["todo","in_progress","blocked","done"];
    async function load(){
      const r = await fetch('/tasks'); const data = await r.json();
      const tb = document.getElementById('tb'); tb.innerHTML = '';
      for (const it of data){
        const tr = document.createElement('tr'); tr.className='row';
        tr.innerHTML = `
          <td>${it.title}</td>
          <td>${it.owner}</td>
          <td>${it.assignee}</td>
          <td><select id="st-${it.id}">${STATUS.map(s=>'<option '+(s===it.status?'selected':'')+'>'+s+'</option>').join('')}</select></td>
          <td class="due">${it.due}</td>
          <td style="min-width:160px"><div class="bar"><div class="fill" id="fill-${it.id}" style="width:${it.progress}%"></div></div></td>
          <td>
            <input type="number" id="pr-${it.id}" min="0" max="100" value="${it.progress}" style="width:80px"/>
            <button onclick="save('${it.id}')">Zapisz</button>
          </td>`;
        tb.appendChild(tr);
      }
    }
    async function save(id){
      const st = document.getElementById('st-'+id).value;
      const pr = Number(document.getElementById('pr-'+id).value||0);
      await fetch('/tasks/'+id+'/status?value='+encodeURIComponent(st), {method:'POST'});
      await fetch('/tasks/'+id+'/progress?value='+pr, {method:'POST'});
      document.getElementById('fill-'+id).style.width = pr + '%';
    }
    load();
  </script>
</body></html>
"""
    return HTMLResponse(html)
