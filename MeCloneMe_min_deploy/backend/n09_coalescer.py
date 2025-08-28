
from __future__ import annotations
import time, hashlib
from typing import Dict, Any, Deque, List, Optional, Tuple
from collections import deque
from pydantic import BaseModel, Field
from fastapi import APIRouter, Query

router = APIRouter()

# ---------- Config ----------
COALESCE_WINDOW_S = 30      # 30s okno łączenia duplikatów
BUFFER_WINDOW_S   = 10      # 10s bufor „preview” do UI
GROUP_TTL_S       = 5*60    # trzymamy grupy 5 min bez nowych zdarzeń

# ---------- Models ----------
class AlertIn(BaseModel):
    source: str = Field(..., description="moduł/nazwa źródła, np. gateway, sms, billing")
    kind: str = Field(..., description="typ zdarzenia, np. timeout, quota, 5xx")
    message: str = Field(..., description="krótki opis")
    severity: str = Field("warn", pattern="^(info|warn|crit)$")
    fingerprint: Optional[str] = Field(None, description="jeśli brak, wyliczymy")
    meta: Dict[str, Any] = Field(default_factory=dict)

class AlertGroup(BaseModel):
    fp: str
    source: str
    kind: str
    last_message: str
    severity: str
    first_ts: int
    last_ts: int
    count: int = 1

    def score(self) -> float:
        sev_w = {"info":1, "warn":5, "crit":10}[self.severity]
        freq   = min(self.count, 10)
        recency = max(1.0, 30.0 / max(1, int(time.time()) - self.last_ts))  # świeższe → wyższe
        return sev_w * freq * recency

# ---------- In‑memory state ----------
ALERT_GROUPS: Dict[str, AlertGroup] = {}
RAW_BUFFER: Deque[Dict[str, Any]] = deque()

def _now() -> int: return int(time.time())

def _fingerprint(inp: AlertIn) -> str:
    raw = (inp.source.strip().lower() + "|" + inp.kind.strip().lower() + "|" + inp.message.strip().lower())
    return hashlib.sha1(raw.encode()).hexdigest()[:16]

def _cleanup() -> None:
    """Sprzątanie starych wpisów w buforze i grupach."""
    now = _now()
    # RAW buffer
    while RAW_BUFFER and (now - (RAW_BUFFER[0].get("ts") or 0) > BUFFER_WINDOW_S):
        RAW_BUFFER.popleft()
    # Groups
    stale = [fp for fp,g in ALERT_GROUPS.items() if now - g.last_ts > GROUP_TTL_S]
    for fp in stale: ALERT_GROUPS.pop(fp, None)

@router.get("/health")
def alerts_health(): return {"ok": True, "groups": len(ALERT_GROUPS), "buffer": len(RAW_BUFFER)}

@router.post("/ingest")
def ingest(inp: AlertIn):
    _cleanup()
    if not inp.fingerprint:
        inp.fingerprint = _fingerprint(inp)
    now = _now()

    # RAW preview buffer
    RAW_BUFFER.append({"ts": now, **inp.dict()})
    # trim in case of burst
    while RAW_BUFFER and len(RAW_BUFFER) > 500: RAW_BUFFER.popleft()

    # Coalesce by fingerprint with sliding window
    g = ALERT_GROUPS.get(inp.fingerprint)
    if g and now - g.last_ts <= COALESCE_WINDOW_S:
        g.count += 1
        g.last_ts = now
        g.last_message = inp.message
        # escalate severity (info < warn < crit)
        order = {"info":0,"warn":1,"crit":2}
        if order[inp.severity] > order[g.severity]: g.severity = inp.severity
    else:
        g = AlertGroup(fp=inp.fingerprint, source=inp.source, kind=inp.kind,
                       last_message=inp.message, severity=inp.severity,
                       first_ts=now, last_ts=now, count=1)
        ALERT_GROUPS[inp.fingerprint] = g
    return {"ok": True, "group": g.dict(), "score": g.score()}

@router.get("")
def list_groups(limit: int = Query(50, ge=1, le=200)):
    _cleanup()
    groups = sorted(ALERT_GROUPS.values(), key=lambda g: (-g.score(), -g.last_ts))
    return {"ok": True, "items": [g.dict() | {"score": g.score()} for g in groups[:limit]]}

@router.get("/buffer")
def list_buffer():
    _cleanup()
    return {"ok": True, "items": list(RAW_BUFFER)}

@router.post("/resolve")
def resolve(fp: str):
    """Ręczne zamknięcie grupy (np. po eskalacji/obsłużeniu)."""
    existed = fp in ALERT_GROUPS
    ALERT_GROUPS.pop(fp, None)
    return {"ok": existed}

