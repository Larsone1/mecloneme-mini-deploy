
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from .n27_progress import progress_html

app = FastAPI(title="MeCloneMe — API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/start")

@app.get("/start", include_in_schema=False)
def start():
    html = """
    <!doctype html><html><head><meta charset="utf-8"/>
    <title>MeCloneMe — API</title>
    <style>body{background:#0b1220;color:#e6f0ff;font-family:Inter,system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:0;padding:40px} a{color:#7dc9ff}</style>
    </head><body>
    <h1>MeCloneMe — API</h1>
    <p>✓ Live</p>
    <ul>
      <li><a href="/alerts/ui">/alerts/ui</a></li>
      <li><a href="/alerts/health">/alerts/health</a></li>
      <li><a href="/progress">/progress</a></li>
      <li><a href="/docs">/docs</a></li>
    </ul>
    </body></html>
    """
    return HTMLResponse(html)

@app.get("/auth/challenge", include_in_schema=False)
def challenge():
    return JSONResponse({"ok": True})

@app.get("/alerts/health", include_in_schema=False)
def health():
    return JSONResponse({"ok": True})

@app.get("/alerts/ui", response_class=HTMLResponse, include_in_schema=False)
def alerts_ui():
    rows = "".join([
        '<tr><td>High error rate</td><td>backend</td><td>88</td></tr>',
        '<tr><td>New signups drop</td><td>analytics</td><td>72</td></tr>',
        '<tr><td>Abandoned carts</td><td>checkout</td><td>61</td></tr>',
    ])
    html = f"""
    <!doctype html><html><head><meta charset="utf-8"/>
    <title>Alerts — MeCloneMe</title>
    <style>
    body{{background:#0b1220;color:#e6f0ff;font-family:Inter,system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:0;padding:32px}}
    table{{border-collapse:collapse;width:100%;max-width:980px}}
    th,td{{padding:10px 12px;border-bottom:1px solid #11223a;text-align:left}}
    th{{opacity:.7}}
    .nav{{margin:18px 0 28px}}
    a{{color:#7dc9ff;text-decoration:none;margin-right:12px}}
    a:hover{{text-decoration:underline}}
    </style></head><body>
    <div class="nav"><a href="/start">Start</a> · <a href="/progress">Progress</a> · <a href="/docs">Docs</a></div>
    <h2>Alerts</h2>
    <table><thead><tr><th>Title</th><th>Source</th><th>Score</th></tr></thead><tbody>{rows}</tbody></table>
    </body></html>
    """
    return HTMLResponse(html)

@app.get("/progress", include_in_schema=False)
def progress():
    return HTMLResponse(progress_html())
