from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from dashboard.config import DashboardConfig
from dashboard.main import create_app

CONFIG_KWARGS = dict(
    slack_client_id="c", slack_client_secret="s", slack_team_id="T1",
    allowed_emails=["a@example.com"], session_secret="secret", api_key="key",
)


@pytest.fixture
def client(tmp_path):
    config = DashboardConfig(db_path=str(tmp_path / "test.db"), **CONFIG_KWARGS)
    app = create_app(config)
    return TestClient(app, follow_redirects=False)


def test_full_login_flow_grants_access_to_protected_pages_then_logout(client):
    # Not logged in: protected pages redirect to /login
    assert client.get("/").status_code == 303
    assert client.get("/servers").status_code == 303
    assert client.get("/errors").status_code == 303

    # Start the Slack login flow to get a real, cookie-backed oauth_state in the session
    login_response = client.get("/login/slack")
    state = login_response.headers["location"].split("state=")[1]

    # Complete the callback with mocked Slack calls (network boundary only)
    with patch("dashboard.routes.auth_routes.auth.exchange_code_for_token", return_value="token"), \
         patch(
             "dashboard.routes.auth_routes.auth.fetch_userinfo",
             return_value={"email": "a@example.com", "team_id": "T1"},
         ):
        callback_response = client.get("/auth/slack/callback", params={"code": "abc", "state": state})

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
