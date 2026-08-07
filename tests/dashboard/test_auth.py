from unittest.mock import MagicMock, patch

import pytest

from dashboard.auth import (
    NotAuthenticated,
    NotAuthorized,
    build_authorize_url,
    exchange_code_for_token,
    fetch_userinfo,
    generate_state,
    is_authorized,
    require_master,
)
from dashboard.config import DashboardConfig

CONFIG = DashboardConfig(
    db_path="x", slack_client_id="client-123", slack_client_secret="secret-456",
    slack_team_id="T12345", master_email="master@example.com",
    session_secret="s", api_key="k",
)


def test_generate_state_returns_nonempty_unique_strings():
    a = generate_state()
    b = generate_state()
    assert a and b and a != b


def test_build_authorize_url_includes_required_params():
    from urllib.parse import parse_qs, urlparse

    url = build_authorize_url(CONFIG, "https://dash.example.com/auth/slack/callback", "state-xyz")

    parsed = urlparse(url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == "https://slack.com/openid/connect/authorize"
    params = parse_qs(parsed.query)
    assert params["client_id"] == ["client-123"]
    assert params["state"] == ["state-xyz"]
    assert params["redirect_uri"] == ["https://dash.example.com/auth/slack/callback"]
    assert params["scope"] == ["openid email profile"]


def test_exchange_code_for_token_returns_access_token():
    fake_response = MagicMock()
    fake_response.json.return_value = {"ok": True, "access_token": "token-abc"}
    fake_response.raise_for_status.return_value = None

    with patch("dashboard.auth.requests.post", return_value=fake_response) as mock_post:
        token = exchange_code_for_token(CONFIG, "code-1", "https://dash.example.com/auth/slack/callback")

    assert token == "token-abc"
    _, kwargs = mock_post.call_args
    assert kwargs["data"]["client_id"] == "client-123"
    assert kwargs["data"]["client_secret"] == "secret-456"
    assert kwargs["data"]["code"] == "code-1"


def test_exchange_code_for_token_raises_on_slack_ok_false():
    fake_response = MagicMock()
    fake_response.json.return_value = {
        "ok": False,
        "error": "invalid_code",
        "access_token": "should-not-be-used",
    }
    fake_response.raise_for_status.return_value = None

    with patch("dashboard.auth.requests.post", return_value=fake_response):
        with pytest.raises(RuntimeError):
            exchange_code_for_token(CONFIG, "code-1", "https://dash.example.com/auth/slack/callback")


def test_fetch_userinfo_raises_on_slack_ok_false():
    fake_response = MagicMock()
    fake_response.json.return_value = {"ok": False, "error": "invalid_auth"}
    fake_response.raise_for_status.return_value = None

    with patch("dashboard.auth.requests.get", return_value=fake_response):
        with pytest.raises(Exception):
            fetch_userinfo("token-abc")


def test_fetch_userinfo_extracts_email_and_team_id():
    fake_response = MagicMock()
    fake_response.json.return_value = {
        "email": "a@example.com",
        "https://slack.com/team_id": "T12345",
    }
    fake_response.raise_for_status.return_value = None

    with patch("dashboard.auth.requests.get", return_value=fake_response) as mock_get:
        userinfo = fetch_userinfo("token-abc")

    assert userinfo == {"email": "a@example.com", "team_id": "T12345"}
    _, kwargs = mock_get.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer token-abc"


def test_is_authorized_true_for_matching_team_and_allowed_email():
    allowed = ["a@example.com"]
    assert is_authorized({"email": "a@example.com", "team_id": "T12345"}, CONFIG, allowed) is True


def test_is_authorized_false_for_wrong_team():
    allowed = ["a@example.com"]
    assert is_authorized({"email": "a@example.com", "team_id": "T99999"}, CONFIG, allowed) is False


def test_is_authorized_false_for_email_not_in_allowlist():
    allowed = ["a@example.com"]
    assert is_authorized({"email": "stranger@example.com", "team_id": "T12345"}, CONFIG, allowed) is False


def test_is_authorized_true_for_master_email_even_when_not_in_allowlist():
    allowed = []
    assert is_authorized(
        {"email": "master@example.com", "team_id": "T12345"}, CONFIG, allowed
    ) is True


class _FakeRequest:
    def __init__(self, session):
        self.session = session


def test_require_master_raises_not_authenticated_when_not_logged_in():
    with pytest.raises(NotAuthenticated):
        require_master(_FakeRequest({}))


def test_require_master_raises_not_authorized_when_logged_in_but_not_master():
    session = {"user": {"email": "a@example.com", "is_master": False}}
    with pytest.raises(NotAuthorized):
        require_master(_FakeRequest(session))


def test_require_master_returns_user_when_master():
    user = {"email": "master@example.com", "is_master": True}
    assert require_master(_FakeRequest({"user": user})) == user
