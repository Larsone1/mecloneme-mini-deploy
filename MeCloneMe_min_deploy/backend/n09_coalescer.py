from __future__ import annotations
import csv, io, time, threading, uuid
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

router = APIRouter(prefix="/alerts", tags=["alerts"])

# ===== In-memory store (demo-grade) =====
_LOCK = threading.Lock()
_ALERTS: Dict[str, Dict[str, Any]] = {}

# ===== Models =====
class AlertIn(BaseModel):
    title: str
    source: str = "system"
    description: Optional[str] = ""
    score: float = Field(ge=0, le=100, default=50)
    group: Optional[str] = "default"
    tags: Optional[List[str]] = []

class AlertOut(BaseModel):
    id: str
    title: str
    source: str
    description: Optional[str]
    score: float
    group: Optional[str]
    tags: List[str]
    status: str
    muted_until: Optional[str]
    created_at: str
    updated_at: str

# ===== Helpers =====
def _now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"

def _cleanup_expired_mutes():
    # No-op needed, mutes are checked dynamically by time
    return

def _to_out(a: Dict[str, Any]) -> AlertOut:
    return AlertOut(
        id=a["id"],
        title=a["title"],
        source=a["source"],
        description=a.get("description", ""),
        score=float(a.get("score", 0)),
        group=a.get("group"),
        tags=list(a.get("tags", [])),
        status=a.get("status", "open"),
        muted_until=(a["muted_until"].isoformat(timespec="seconds") + "Z") if a.get("muted_until") else None,
        created_at=a["created_at"].isoformat(timespec="seconds") + "Z",
        updated_at=a["updated_at"].isoformat(timespec="seconds") + "Z",
    )

# ===== Seed for demo =====
def _seed_if_empty():
    with _LOCK:
        if _ALERTS:
            return
        now = datetime.utcnow()
        samples = [
            {"title":"High error rate","source":"backend","description":"5xx spiked","score":88,"group":"SRE","tags":["api","errors"]},
            {"title":"New signups drop","source":"analytics","description":"-35% vs avg","score":72,"group":"Growth","tags":["funnel"]},
            {"title":"Abandoned carts","source":"checkout","description":"+23% vs avg","score":61,"group":"Revenue","tags":["shop"]},
        ]
        for s in samples:
            aid = uuid.uuid4().hex[:10]
            _ALERTS[aid] = {
                "id": aid,
                "title": s["title"],
                "source": s["source"],
                "description": s.get("description",""),
                "score": float(s.get("score",50)),
                "group": s.get("group","default"),
                "tags": list(s.get("tags",[])),
                "status": "open",
                "muted_until": None,
                "created_at": now - timedelta(minutes=15),
                "updated_at": now - timedelta(minutes=5),
            }

# ===== Endpoints =====
@router.get("/health")
def health() -> Dict[str, Any]:
    _cleanup_expired_mutes()
    return {"ok": True, "count": len(_ALERTS)}

@router.get("", response_model=List[AlertOut])
def list_alerts(
    limit: int = Query(100, ge=1, le=1000),
    min_score: float = Query(0, ge=0, le=100),
    status: Optional[str] = Query(None, pattern="^(open|resolved)$"),
    q: Optional[str] = None,
    include_muted: bool = False,
    sort: str = Query("-score", description="field or -field; supported: score, updated_at, created_at"),
):
    _seed_if_empty()
    with _LOCK:
        rows = list(_ALERTS.values())
    now = datetime.utcnow()
    def _is_muted(a):
        mu = a.get("muted_until")
        return bool(mu and mu > now)
    # filters
    out = []
    for a in rows:
        if a.get("score",0) < min_score:
            continue
        if status and a.get("status","open") != status:
            continue
        if not include_muted and _is_muted(a):
            continue
        if q:
            hay = " ".join([a.get("title",""), a.get("description",""), a.get("source",""), " ".join(a.get("tags",[]))]).lower()
            if q.lower() not in hay:
                continue
        out.append(a)
    # sort
    key = sort.lstrip("-")
    reverse = sort.startswith("-")
    def _key(a):
        if key in ("updated_at","created_at"):
            return a.get(key) or datetime.min
        return a.get(key, 0)
    out.sort(key=_key, reverse=reverse)
    # limit
    out = out[:limit]
    return [_to_out(a) for a in out]

@router.post("/ingest", response_model=List[AlertOut])
def ingest(items: List[AlertIn]):
    now = datetime.utcnow()
    outs = []
    with _LOCK:
        for it in items:
            aid = uuid.uuid4().hex[:10]
            rec = {
                "id": aid,
                "title": it.title,
                "source": it.source,
                "description": it.description or "",
                "score": float(it.score),
                "group": it.group or "default",
                "tags": list(it.tags or []),
                "status": "open",
                "muted_until": None,
                "created_at": now,
                "updated_at": now,
            }
            _ALERTS[aid] = rec
            outs.append(_to_out(rec))
    return outs

@router.post("/{aid}/resolve", response_model=AlertOut)
def resolve(aid: str):
    with _LOCK:
        a = _ALERTS.get(aid)
        if not a:
            raise HTTPException(404, "Alert not found")
        a["status"] = "resolved"
        a["updated_at"] = datetime.utcnow()
        return _to_out(a)

@router.post("/{aid}/mute", response_model=AlertOut)
def mute(aid: str, minutes: int = Query(15, ge=1, le=10080)):
    with _LOCK:
        a = _ALERTS.get(aid)
        if not a:
            raise HTTPException(404, "Alert not found")
        a["muted_until"] = datetime.utcnow() + timedelta(minutes=minutes)
        a["updated_at"] = datetime.utcnow()
        return _to_out(a)

@router.get(".csv")
def export_csv():
    _seed_if_empty()
    with _LOCK:
        rows = list(_ALERTS.values())
    f = io.StringIO()
    w = csv.writer(f)
    w.writerow(["id","title","source","score","status","muted_until","created_at","updated_at","group","tags"])
    for a in rows:
        w.writerow([
            a["id"], a["title"], a["source"], a["score"], a.get("status","open"),
            a["muted_until"].isoformat() if a.get("muted_until") else "",
            a["created_at"].isoformat(), a["updated_at"].isoformat(),
            a.get("group",""), " ".join(a.get("tags",[]))
        ])
    f.seek(0)
    return StreamingResponse(iter([f.getvalue()]), media_type="text/csv", headers={"Content-Disposition":"attachment; filename=alerts.csv"})

@router.get("/ui", response_class=HTMLResponse)
def ui():
    # very small, dependency-free UI
    html = f"""
    <!doctype html>
    <html><head>
      <meta charset="utf-8"/>
      <meta name="viewport" content="width=device-width, initial-scale=1"/>
      <title>Alerts • MeCloneMe</title>
      <style>
        body{{background:#0b0f14;color:#e5e7eb;font-family:Inter,system-ui,Segoe UI,Roboto,Helvetica,Arial,sans-serif;margin:0;padding:24px}}
        h1{{font-size:20px;margin:0 0 12px}}
        .row{{display:flex;gap:12px;align-items:center;margin-bottom:12px;flex-wrap:wrap}}
        input,select,button{{background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:10px;padding:8px 10px}}
        table{{width:100%;border-collapse:separate;border-spacing:0 8px}}
        th,td{{padding:10px 12px;text-align:left}}
        tr{{background:#0f172a}}
        .tag{{display:inline-block;background:#111827;border:1px solid #374151;border-radius:999px;padding:2px 8px;margin-right:6px;font-size:12px}}
        .spark{{height:28px;width:120px}}
        .muted{{opacity:.5}}
      </style>
    </head>
    <body>
      <h1>Alerts</h1>
      <div class="row">
        <label>Min score <input id="min_score" type="number" value="0" min="0" max="100" style="width:80px"/></label>
        <label>Status 
          <select id="status">
            <option value="">all</option>
            <option>open</option>
            <option>resolved</option>
          </select>
        </label>
        <input id="q" placeholder="search title/source/tags" style="min-width:220px"/>
        <button onclick="load()">Refresh</button>
        <a href=".csv" style="margin-left:auto;color:#93c5fd">Download CSV</a>
      </div>
      <table id="tbl">
        <thead>
          <tr><th>Title</th><th>Score</th><th>Source</th><th>Tags</th><th>Updated</th><th>Trend</th><th>Actions</th></tr>
        </thead>
        <tbody></tbody>
      </table>
      <script>
        async function load(){{
          const params = new URLSearchParams();
          const ms = document.getElementById('min_score').value||0;
          const st = document.getElementById('status').value;
          const q = document.getElementById('q').value;
          if (ms) params.set('min_score', ms);
          if (st) params.set('status', st);
          if (q) params.set('q', q);
          const res = await fetch('/alerts?'+params.toString());
          const data = await res.json();
          const tb = document.querySelector('#tbl tbody');
          tb.innerHTML = '';
          for (const a of data){{
            const tr = document.createElement('tr');
            if (a.muted_until) tr.classList.add('muted');
            const tags = (a.tags||[]).map(t=>`<span class='tag'>${{t}}</span>`).join('');
            tr.innerHTML = `
              <td>${{a.title}}</td>
              <td>${{a.score.toFixed(0)}}</td>
              <td>${{a.source}}</td>
              <td>${{tags}}</td>
              <td>${{a.updated_at.replace('T',' ')}}</td>
              <td><svg class='spark' viewBox='0 0 100 20'><polyline fill='none' stroke='#34d399' stroke-width='2' points='0,15 20,10 40,12 60,6 80,8 100,4' /></svg></td>
              <td>
                <button onclick="act('resolve','${{a.id}}')">Resolve</button>
                <button onclick="act('mute','${{a.id}}',15)">Mute 15m</button>
                <button onclick="act('mute','${{a.id}}',60)">Mute 60m</button>
              </td>`;
            tb.appendChild(tr);
          }}
        }}
        async function act(kind,id,minutes){{
          const url = kind==='resolve' ? `/alerts/${{id}}/resolve` : `/alerts/${{id}}/mute?minutes=${{minutes}}`;
          await fetch(url, {{method:'POST'}});
          load();
        }}
        load();
      </script>
    </body></html>
    """
    return HTMLResponse(html)
