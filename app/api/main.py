"""Minimal database-backed demo API; no incident or agent behavior."""

import logging
import os

import psycopg
from fastapi import FastAPI, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, generate_latest

app = FastAPI(title="IncidentPilot demo API")
logger = logging.getLogger(__name__)
registry = CollectorRegistry()
requests = Counter(
    "demo_api_requests_total",
    "Database-backed demo requests by outcome.",
    ["outcome"],
    registry=registry,
)
for outcome in ("success", "error"):
    requests.labels(outcome=outcome)


def connect():
    return psycopg.connect(
        host=os.environ["DB_HOST"],
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        connect_timeout=3,
    )


@app.get("/livez")
def live():
    return {"status": "alive"}


@app.get("/healthz")
def health():
    try:
        with connect() as connection:
            connection.execute("SELECT 1").fetchone()
    except psycopg.Error:
        logger.warning("Database health check failed")
        raise HTTPException(status_code=503, detail="Database unavailable") from None
    return {"status": "healthy", "database": "reachable"}


@app.get("/api/message")
def message():
    try:
        with connect() as connection:
            row = connection.execute(
                "SELECT message FROM demo_messages WHERE id = 1"
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=503, detail="Demo data unavailable")
    except psycopg.Error:
        requests.labels(outcome="error").inc()
        logger.warning("Database-backed demo request failed")
        raise HTTPException(status_code=503, detail="Database unavailable") from None
    except HTTPException:
        requests.labels(outcome="error").inc()
        raise
    requests.labels(outcome="success").inc()
    return {"message": row[0], "source": "postgres"}


@app.get("/metrics", include_in_schema=False)
def metrics():
    return Response(content=generate_latest(registry), headers={"Content-Type": CONTENT_TYPE_LATEST})
