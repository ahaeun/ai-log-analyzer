from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from dashboard import db
from dashboard.config import DashboardConfig
from dashboard.main import create_app

CONFIG_KWARGS = dict(
    slack_client_id="c", slack_client_secret="s", slack_team_id="T1",
    master_email="master@example.com", session_secret="secret", api_key="key",
)


@pytest.fixture
def config(tmp_path):
    return DashboardConfig(db_path=str(tmp_path / "test.db"), **CONFIG_KWARGS)


@pytest.fixture
def client(config):
    app = create_app(config)
    return TestClient(app, follow_redirects=False)


def _login_as(client, email, team_id="T1"):
    login_response = client.get("/login/slack")
    state = login_response.headers["location"].split("state=")[1]
    with patch("dashboard.routes.auth_routes.auth.exchange_code_for_token", return_value="token"), \
         patch(
             "dashboard.routes.auth_routes.auth.fetch_userinfo",
             return_value={"email": email, "team_id": team_id},
         ):
        return client.get("/auth/slack/callback", params={"code": "abc", "state": state})


def test_full_login_flow_grants_access_to_protected_pages_then_logout(client):
    # Not logged in: protected pages redirect to /login
    assert client.get("/").status_code == 303
    assert client.get("/servers").status_code == 303
    assert client.get("/errors").status_code == 303

    # Complete the callback with mocked Slack calls (network boundary only)
    callback_response = _login_as(client, "master@example.com")

    assert callback_response.status_code == 303
    assert callback_response.headers["location"] == "/"

    # Now logged in via the REAL session cookie (not dependency_overrides): protected pages work
    assert client.get("/").status_code == 200
    assert client.get("/servers").status_code == 200
    assert client.get("/errors").status_code == 200

    # Logout clears the session
    logout_response = client.post("/logout")
    assert logout_response.status_code == 303
    assert client.get("/").status_code == 303


def test_master_email_can_manage_allowed_email_list(client, config):
    _login_as(client, "master@example.com")

    assert client.get("/settings/emails").status_code == 200

    add_response = client.post("/settings/emails", data={"email": "member@example.com"})
    assert add_response.status_code == 303
    assert [row["email"] for row in db.list_allowed_emails(config.db_path)] == ["member@example.com"]

    delete_response = client.post("/settings/emails/member@example.com/delete")
    assert delete_response.status_code == 303
    assert db.list_allowed_emails(config.db_path) == []


def test_non_master_allowed_user_cannot_manage_email_list(client, config):
    db.add_allowed_email(config.db_path, "member@example.com")
    _login_as(client, "member@example.com")

    assert client.get("/settings/emails").status_code == 403
