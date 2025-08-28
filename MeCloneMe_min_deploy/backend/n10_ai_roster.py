
from __future__ import annotations
import json, os, threading
from typing import Dict, Any
from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from datetime import datetime

router = APIRouter(prefix="/ai", tags=["ai"])

_LOCK = threading.Lock()
_PATH = os.environ.get("MC_AI_PATH", "/tmp/mecloneme_ai_roster.json")

def _now():
    return datetime.utcnow().isoformat(timespec="seconds")+"Z"

def _seed() -> Dict[str, Any]:
    return {
        "ceo": {"id":"ceo","name":"CEO Klon AI","role":"Zarząd","online":True,"notes":"Nadzór nad całością","updated_at":_now()},
        "legal": {"id":"legal","name":"Prawnik AI","role":"Prawny","online":True,"notes":"Umowy, jurysdykcje","updated_at":_now()},
        "marketing": {"id":"marketing","name":"Julia — Marketing AI","role":"Marketing","online":True,"notes":"Kampanie, ROI","updated_at":_now()},
        "log": {"id":"log","name":"Logistyka AI","role":"Operacje","online":False,"notes":"Dostawy","updated_at":_now()},
        "fin": {"id":"fin","name":"Finanse AI","role":"Finanse","online":True,"notes":"Budżety, cashflow","updated_at":_now()},
    }

def _load() -> Dict[str, Any]:
    with _LOCK:
        try:
            with open(_PATH,"r",encoding="utf-8") as f:
                data=json.load(f)
                if data: return data
        except Exception:
            pass
        data=_seed()
        try:
            with open(_PATH,"w",encoding="utf-8") as f: json.dump(data,f)
        except Exception: pass
        return data

@router.get("/ui", response_class=HTMLResponse)
def ui():
    data = _load()
    roles = {}
    for v in data.values():
        roles.setdefault(v["role"], 0)
        roles[v["role"]] += 1
    html = f"""
<!doctype html><html><head>
  <meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>AI Roster — MeCloneMe</title>
  <style>
    body{{background:#0b0f14;color:#e5e7eb;font-family:Inter,system-ui,Segoe UI,Roboto,Helvetica,Arial,sans-serif;margin:0;padding:24px}}
    a{{color:#93c5fd}}
    .wrap{{max-width:1080px;margin:auto}}
    .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}}
    .card{{background:#0f172a;border:1px solid #1f2937;border-radius:16px;padding:14px}}
    .dot{{display:inline-block;width:8px;height:8px;border-radius:999px;margin-right:6px}}
    .on{{background:#22c55e}} .off{{background:#ef4444}}
  </style>
</head><body>
  <div class="wrap">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
      <h1 style="margin:0">AI Roster</h1>
      <a href="/" style="text-decoration:none"><button style="background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:10px;padding:6px 10px">START</button></a>
    </div>
    <p style="opacity:.8">Zespoły: {" • ".join([f"{k} ({v})" for k,v in roles.items()])}</p>
    <div class="grid">
      {"".join([f'<div class="card"><div><span class="dot {"on" if v["online"] else "off"}"></span><b>{v["name"]}</b></div><div style="opacity:.8">{v["role"]}</div><p style="margin:8px 0 0">{v["notes"]}</p></div>' for v in data.values()])}
    </div>
  </div>
</body></html>
"""
    return HTMLResponse(html)
