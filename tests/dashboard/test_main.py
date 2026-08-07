import pytest

from dashboard.config import DashboardConfig
from dashboard.main import create_app


@pytest.fixture
def config(tmp_path):
    return DashboardConfig(db_path=str(tmp_path / "test.db"), slack_client_id="c",
                            slack_client_secret="s", slack_team_id="T1",
                            allowed_emails=["a@example.com"], session_secret="secret", api_key="key")


def test_create_app_initializes_db_and_mounts_routes(config):
    app = create_app(config)

    from fastapi.testclient import TestClient
    client = TestClient(app, follow_redirects=False)

    assert client.get("/login").status_code == 200
    assert client.get("/api/servers", headers={"X-API-Key": "key"}).status_code == 200
    assert client.get("/").status_code == 303  # not logged in -> redirect
