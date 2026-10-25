from fastapi.testclient import TestClient
from backend.app.main import app

def test_health_identifies_testnet():
    with TestClient(app) as client:
        data=client.get("/api/health").json()
        assert data["ok"] is True
        assert data["environment"]=="TESTNET"

def test_trading_control_requires_authorization():
    with TestClient(app) as client:
        response=client.post("/api/bot/start")
        assert response.status_code==401

def test_live_execution_is_not_enabled():
    with TestClient(app) as client:
        response=client.post("/api/bot/start",headers={"Authorization":"Bearer change-me-before-enabling-controls"})
        assert response.status_code==401

def test_connection_status_reports_latest_check():
    with TestClient(app) as client:
        data=client.get("/api/exchange/status").json()
        assert data["environment"]=="TESTNET"
        assert isinstance(data["connected"],bool)
        if data["connected"]:
            assert data["checked_at"] is not None

def test_risk_status_is_honest_without_account_equity():
    with TestClient(app) as client:
        data=client.get("/api/risk/status").json()
        assert data["available"] is False

