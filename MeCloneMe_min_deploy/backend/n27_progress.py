
from typing import List, Tuple
PROGRESS: List[Tuple[str,int]] = [
    ("N01 — SSOT / Router-README", 55),
    ("N04 — Mobile (Camera/Mic)", 20),
    ("N05 — Desktop (Bridge)", 20),
    ("N09 — Guardian", 30),
    ("N18 — Panel CEO", 35),
    ("N21 — SDK / API Clients", 15),
    ("N22 — Testy & QA", 25),
    ("N27 — Docs & OpenAPI", 30),
    ("N30 — Core (Live+AR+Guardian)", 40),
]
def progress_html() -> str:
    items = []
    for label, pct in PROGRESS:
        items.append(f"""
        <div class="card">
          <div class="row">
            <div class="label">{label}</div><div class="pct">{pct}%</div>
          </div>
          <div class="bar"><div class="fill" style="width:{pct}%;"></div></div>
        </div>""")
    return f"""
<!doctype html>
<html>
<head>
<meta charset="utf-8"/>
<title>Postęp MeCloneMe</title>
<style>
  body{{background:#0b1220;color:#e6f0ff;font-family:Inter,system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif; margin:0; padding:32px;}}
  h1{{font-weight:700;letter-spacing:.2px;margin:0 0 12px}}
  .note{{opacity:.6;margin-bottom:20px}}
  .grid{{display:grid; gap:14px; max-width:880px}}
  .card{{background:#0f1a2b;border-radius:14px;padding:16px 18px;border:1px solid #11223a;box-shadow:0 1px 0 #0b1526 inset;}}
  .row{{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}}
  .pct{{opacity:.7}}
  .bar{{height:12px;border-radius:999px;background:#0c223a; overflow:hidden;box-shadow:inset 0 0 0 1px #0d2a46}}
  .fill{{height:100%;background:linear-gradient(90deg,#41d99d,#2fb784); border-radius:999px}}
  .nav{{margin-top:24px;}}
  a{{color:#7dc9ff;text-decoration:none;margin-right:12px}}
  a:hover{{text-decoration:underline}}
</style>
</head>
<body>
<h1>Postęp MeCloneMe</h1>
<div class="note">Zielony = zrealizowane, jaśniejszy szary = pozostały zakres</div>
<div class="grid">
{''.join(items)}
</div>
<div class="nav">
  <a href="/start">Start</a> ·
  <a href="/alerts/ui">Alerts</a> ·
  <a href="/docs">Docs</a>
</div>
</body>
</html>
"""
