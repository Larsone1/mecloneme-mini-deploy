from __future__ import annotations
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _safe_load():
    alerts = []
    progress = []
    tasks = []
    try:
        from backend import n09_coalescer as A

        alerts = list(A._load().values())  # type: ignore
    except Exception:
        pass
    try:
        from backend import n27_progress as P

        progress = list(P._load().values())  # type: ignore
    except Exception:
        pass
    try:
        from backend import n28_tasks as T

        tasks = list(T._load().values())  # type: ignore
    except Exception:
        pass
    return alerts, progress, tasks


@router.get("/ui", response_class=HTMLResponse)
def ui():
    alerts, progress, tasks = _safe_load()
    open_alerts = sum(1 for a in alerts if a.get("status") == "open")
    resolved_alerts = sum(1 for a in alerts if a.get("status") == "resolved")
    avg_progress = 0
    if progress:
        avg_progress = int(
            sum(int(p.get("percent", 0)) for p in progress) / len(progress)
        )
    t_todo = sum(1 for t in tasks if t.get("status") == "todo")
    t_prog = sum(1 for t in tasks if t.get("status") == "in_progress")
    t_block = sum(1 for t in tasks if t.get("status") == "blocked")
    t_done = sum(1 for t in tasks if t.get("status") == "done")
    html = f"""
<!doctype html><html><head>
  <meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Dashboard — MeCloneMe</title>
  <style>
    body{{background:#0b0f14;color:#e5e7eb;font-family:Inter,system-ui,Segoe UI,Roboto,Helvetica,Arial,sans-serif;margin:0;padding:24px}}
    a{{color:#93c5fd}}
    .wrap{{max-width:1080px;margin:auto}}
    .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}}
    .card{{background:#0f172a;border:1px solid #1f2937;border-radius:16px;padding:14px}}
    .num{{font-size:28px;font-weight:800}}
    .row{{display:flex;gap:8px;flex-wrap:wrap}}
    .pill{{background:#111827;border:1px solid #374151;border-radius:999px;padding:4px 10px}}
    button{{background:#111827;color:#e5e7eb;border:1px solid #374151;border-radius:10px;padding:6px 10px;cursor:pointer}}
  </style>
</head><body>
  <div class="wrap">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
      <h1 style="margin:0">Dashboard</h1>
      <a href="/" style="text-decoration:none"><button>START</button></a>
    </div>
    <div class="grid">
      <div class="card"><div>Alerty — otwarte</div><div class="num">{open_alerts}</div><div class="row"><span class="pill"><a href="/alerts/ui">Zobacz</a></span></div></div>
      <div class="card"><div>Alerty — resolved</div><div class="num">{resolved_alerts}</div></div>
      <div class="card"><div>Średni progres</div><div class="num">{avg_progress}%</div><div class="row"><span class="pill"><a href="/progress/ui">Postęp</a></span></div></div>
      <div class="card"><div>Zadania</div><div class="num">{t_done}/{t_todo+t_prog+t_block+t_done} done</div><div class="row"><span class="pill">todo {t_todo}</span><span class="pill">in_progress {t_prog}</span><span class="pill">blocked {t_block}</span></div></div>
    </div>
  </div>
</body></html>
"""
    return HTMLResponse(html)
