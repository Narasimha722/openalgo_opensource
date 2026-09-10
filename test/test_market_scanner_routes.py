"""Scanner route access controls, CSRF and app registration without app startup."""

from datetime import datetime

import pytest
from flask import Flask
from flask_wtf.csrf import CSRFProtect, generate_csrf

from blueprints import market_scanner as routes
from services.market_scanner_service import IST


@pytest.fixture
def client(monkeypatch):
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="scanner-test-only")
    CSRFProtect(app)
    app.register_blueprint(routes.market_scanner_bp)
    app.get("/test-csrf")(lambda: {"token": generate_csrf()})
    monkeypatch.setattr(routes, "get_fyers_token", lambda user: "test-token")
    with app.test_client() as client:
        yield client


def login(client, broker="fyers"):
    with client.session_transaction() as session:
        session.update(
            user="alice", logged_in=True, broker=broker, login_time=datetime.now(IST).isoformat()
        )


def headers(client):
    return {"X-CSRFToken": client.get("/test-csrf").json["token"]}


def test_requires_logged_in_fyers_session(client):
    assert client.get("/market-scanner/api/results").status_code == 401
    login(client, "zerodha")
    assert client.get("/market-scanner/api/results").status_code == 403


def test_csrf_is_required_for_start_and_cancel(client):
    login(client)
    for endpoint in ("scan", "cancel"):
        assert client.post(f"/market-scanner/api/{endpoint}", json={}).status_code == 400


def test_start_is_async_and_results_use_authenticated_user(client, monkeypatch):
    login(client)
    calls = []
    monkeypatch.setattr(
        routes.scanner_manager,
        "start",
        lambda user, data: (calls.append((user, data)) or {"state": "running"}, False),
    )
    response = client.post(
        "/market-scanner/api/scan", json={"min_rvol": 2}, headers=headers(client)
    )
    assert response.status_code == 202
    assert response.headers["Cache-Control"] == "no-store"
    assert calls == [("alice", {"min_rvol": 2})]
    monkeypatch.setattr(
        routes.scanner_manager,
        "results",
        lambda user, filters: {"user_for_test": user, "filters": filters},
    )
    result = client.get("/market-scanner/api/results?limit=10").json["data"]
    assert result == {"user_for_test": "alice", "filters": {"limit": "10"}}


def test_universe_and_cancel_routes(client, monkeypatch):
    login(client)
    monkeypatch.setattr(routes, "load_universe", lambda: [{"symbol": "ABC"}])
    assert client.get("/market-scanner/api/universe").json["data"]["count"] == 1
    monkeypatch.setattr(routes.scanner_manager, "cancel", lambda user: {"state": "cancelled"})
    assert (
        client.post("/market-scanner/api/cancel", json={}, headers=headers(client)).json["data"][
            "state"
        ]
        == "cancelled"
    )


def test_broker_exceptions_do_not_expose_credentials(client, monkeypatch):
    login(client)

    def fail(user):
        raise RuntimeError("sensitive-test-token")

    monkeypatch.setattr(routes, "get_fyers_token", fail)
    response = client.get("/market-scanner/api/results")
    assert response.status_code == 500
    assert "sensitive-test-token" not in response.get_data(as_text=True)
