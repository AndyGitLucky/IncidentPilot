from unittest.mock import MagicMock

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.api import main

client = TestClient(main.app)


@pytest.fixture
def database(monkeypatch):
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.execute.return_value.fetchone.return_value = ("Hello from PostgreSQL.",)
    monkeypatch.setattr(main, "connect", lambda: connection)
    return connection


def test_health_checks_database(database):
    assert client.get("/healthz").json() == {
        "status": "healthy", "database": "reachable"
    }
    database.execute.assert_called_once_with("SELECT 1")


def test_database_outage_fails_readiness_but_keeps_liveness(monkeypatch):
    def unavailable():
        raise psycopg.OperationalError("sensitive connection details")

    monkeypatch.setattr(main, "connect", unavailable)
    assert client.get("/livez").status_code == 200
    for path in ("/healthz", "/api/message"):
        response = client.get(path)
        assert response.status_code == 503
        assert response.json() == {"detail": "Database unavailable"}


def test_message_reads_database_and_counts_success(database):
    before = main.requests.labels(outcome="success")._value.get()
    response = client.get("/api/message")
    assert response.status_code == 200
    assert response.json() == {"message": "Hello from PostgreSQL.", "source": "postgres"}
    assert main.requests.labels(outcome="success")._value.get() == before + 1


def test_missing_seed_data_is_unhealthy(database):
    database.execute.return_value.fetchone.return_value = None
    before = main.requests.labels(outcome="error")._value.get()
    assert client.get("/api/message").status_code == 503
    assert main.requests.labels(outcome="error")._value.get() == before + 1


def test_metrics_are_prometheus_text():
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain; version=0.0.4")
    assert '# TYPE demo_api_requests_total counter' in response.text
    assert 'demo_api_requests_total{outcome="success"}' in response.text
    assert 'demo_api_requests_total{outcome="error"}' in response.text
