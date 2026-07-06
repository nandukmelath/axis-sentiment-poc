"""FastAPI layer — exposes the marts as REST so other Axis systems can consume them.
Optional access control: set AXIS_API_KEY in the env and pass it as the `x-api-key` header.

Run:  uvicorn api.main:app --host 0.0.0.0 --port 8600
Docs: http://localhost:8600/docs
"""
import os
import time
from collections import defaultdict, deque

from fastapi import FastAPI, Header, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import db

app = FastAPI(title="Axis Social Intelligence API", version="1.0",
              description="Read-only access to the sentiment marts.")

# CORS — restrict to configured origins (comma-separated) or '*' for dev.
_origins = os.getenv("AXIS_API_CORS", "*").split(",")
app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_methods=["GET"], allow_headers=["*"])

# Lightweight in-memory rate limit (per-IP, per-minute) — no extra dependency.
_RATE = int(os.getenv("AXIS_API_RATE_PER_MIN", "120"))
_hits = defaultdict(deque)


@app.middleware("http")
async def _rate_limit(request: Request, call_next):
    ip = request.client.host if request.client else "?"
    now = time.time()
    q = _hits[ip]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= _RATE:
        return JSONResponse(status_code=429, content={"detail": "rate limit exceeded"})
    q.append(now)
    return await call_next(request)


def auth(x_api_key: str = Header(default=None)):
    want = os.getenv("AXIS_API_KEY")
    if want and x_api_key != want:
        raise HTTPException(status_code=401, detail="invalid or missing x-api-key")
    return True


@app.get("/ready")
def ready():
    try:
        db.df("SELECT 1 AS ok")
        return {"status": "ready", "db": "ok"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"db not ready: {str(e)[:80]}")


def _rows(sql, params=None):
    try:
        d = db.df(sql, params) if params else db.df(sql)
        return d.to_dict("records")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)[:150])


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/kpis", dependencies=[Depends(auth)])
def kpis():
    r = _rows("SELECT * FROM mart_kpis")
    return r[0] if r else {}


@app.get("/clusters", dependencies=[Depends(auth)])
def clusters(limit: int = 10, offset: int = 0):
    return _rows(f"SELECT title, size, top_team, recent_share, avg_score FROM clusters "
                 f"ORDER BY size DESC LIMIT {int(limit)} OFFSET {int(offset)}")


@app.get("/competitor-sov", dependencies=[Depends(auth)])
def competitor_sov():
    return _rows("SELECT * FROM mart_competitor_sov ORDER BY mentions DESC")


@app.get("/alerts", dependencies=[Depends(auth)])
def alerts():
    return _rows("SELECT kind, severity, title, detail, created_at FROM alerts ORDER BY created_at DESC")


@app.get("/churn", dependencies=[Depends(auth)])
def churn(limit: int = 20, offset: int = 0):
    return _rows(f"SELECT * FROM mart_churn_risk ORDER BY churn_prob DESC "
                 f"LIMIT {int(limit)} OFFSET {int(offset)}")


@app.get("/products", dependencies=[Depends(auth)])
def products(limit: int = 100, offset: int = 0):
    return _rows(f"SELECT * FROM mart_product_scorecard ORDER BY mentions DESC "
                 f"LIMIT {int(limit)} OFFSET {int(offset)}")


@app.get("/forecast", dependencies=[Depends(auth)])
def forecast():
    return _rows("SELECT * FROM mart_forecast ORDER BY category, horizon_day")


@app.get("/entities", dependencies=[Depends(auth)])
def entities():
    return _rows("SELECT * FROM mart_entities ORDER BY mentions DESC")


@app.get("/cost", dependencies=[Depends(auth)])
def cost():
    r = _rows("SELECT * FROM run_metrics ORDER BY run_ts DESC LIMIT 1")
    return r[0] if r else {}


@app.get("/rm/{customer_key}", dependencies=[Depends(auth)])
def rm(customer_key: str):
    r = _rows("SELECT * FROM mart_rm_enablement WHERE customer_key = :c", {"c": customer_key})
    if not r:
        raise HTTPException(status_code=404, detail="customer not found")
    return r[0]
