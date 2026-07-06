"""FastAPI layer — exposes the marts as REST so other Axis systems can consume them.
Optional access control: set AXIS_API_KEY in the env and pass it as the `x-api-key` header.

Run:  uvicorn api.main:app --host 0.0.0.0 --port 8600
Docs: http://localhost:8600/docs
"""
import os
from fastapi import FastAPI, Header, HTTPException, Depends

import db

app = FastAPI(title="Axis Social Intelligence API", version="1.0",
              description="Read-only access to the sentiment marts.")


def auth(x_api_key: str = Header(default=None)):
    want = os.getenv("AXIS_API_KEY")
    if want and x_api_key != want:
        raise HTTPException(status_code=401, detail="invalid or missing x-api-key")
    return True


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
def clusters(limit: int = 10):
    return _rows(f"SELECT title, size, top_team, recent_share, avg_score FROM clusters "
                 f"ORDER BY size DESC LIMIT {int(limit)}")


@app.get("/competitor-sov", dependencies=[Depends(auth)])
def competitor_sov():
    return _rows("SELECT * FROM mart_competitor_sov ORDER BY mentions DESC")


@app.get("/alerts", dependencies=[Depends(auth)])
def alerts():
    return _rows("SELECT kind, severity, title, detail, created_at FROM alerts ORDER BY created_at DESC")


@app.get("/churn", dependencies=[Depends(auth)])
def churn(limit: int = 20):
    return _rows(f"SELECT * FROM mart_churn_risk ORDER BY churn_prob DESC LIMIT {int(limit)}")


@app.get("/products", dependencies=[Depends(auth)])
def products():
    return _rows("SELECT * FROM mart_product_scorecard ORDER BY mentions DESC")


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
