from unittest.mock import patch

import pytest
from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware
from fastapi.testclient import TestClient

from dashboard import db
from dashboard.config import DashboardConfig
from dashboard.routes.auth_routes import auth_router


@pytest.fixture
def config(tmp_path):
    config = DashboardConfig(
        db_path=str(tmp_path / "test.db"), slack_client_id="client-123", slack_client_secret="secret-456",
        slack_team_id="T12345", master_email="master@example.com",
        session_secret="test-session-secret", api_key="k",
    )
    db.init_db(config.db_path)
    db.add_allowed_email(config.db_path, "a@example.com")
    return config


@pytest.fixture
def client(config):
    app = FastAPI()
    app.state.config = config
    app.add_middleware(SessionMiddleware, secret_key=config.session_secret)
    app.include_router(auth_router)
    return TestClient(app, follow_redirects=False)


def test_login_page_renders(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert "Slack" in response.text


def test_login_slack_redirects_to_slack_authorize(client):
    response = client.get("/login/slack")
    assert response.status_code in (302, 307)
    assert response.headers["location"].startswith("https://slack.com/openid/connect/authorize")


def test_callback_rejects_state_mismatch(client):
    client.get("/login/slack")  # sets session["oauth_state"]

    response = client.get("/auth/slack/callback", params={"code": "abc", "state": "wrong"})

    assert response.status_code in (302, 303)
    assert response.headers["location"].startswith("/login")


def test_callback_rejects_unauthorized_user(client):
    login_response = client.get("/login/slack")
    state = login_response.headers["location"].split("state=")[1]

    with patch("dashboard.routes.auth_routes.auth.exchange_code_for_token", return_value="token"), \
         patch(
             "dashboard.routes.auth_routes.auth.fetch_userinfo",
             return_value={"email": "stranger@example.com", "team_id": "T12345"},
         ):
        response = client.get("/auth/slack/callback", params={"code": "abc", "state": state})

    assert response.status_code == 403


def test_callback_sets_session_for_authorized_user(client):
    login_response = client.get("/login/slack")
    state = login_response.headers["location"].split("state=")[1]

    with patch("dashboard.routes.auth_routes.auth.exchange_code_for_token", return_value="token"), \
         patch(
             "dashboard.routes.auth_routes.auth.fetch_userinfo",
             return_value={"email": "a@example.com", "team_id": "T12345"},
         ):
        response = client.get("/auth/slack/callback", params={"code": "abc", "state": state})

    assert response.status_code in (302, 303)
    assert response.headers["location"] == "/"


def test_logout_clears_session(client):
    response = client.post("/logout")
    assert response.status_code in (302, 303)
    assert response.headers["location"] == "/login"
