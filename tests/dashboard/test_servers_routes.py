from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from dashboard import db
from dashboard.auth import NotAuthenticated, require_session
from dashboard.config import DashboardConfig
from dashboard.routes.servers_routes import servers_router

CONFIG_KWARGS = dict(
    slack_client_id="c", slack_client_secret="s", slack_team_id="T1",
    allowed_emails=["a@example.com"], session_secret="secret", api_key="k",
)


@pytest.fixture
def app(tmp_path):
    config = DashboardConfig(db_path=str(tmp_path / "test.db"), **CONFIG_KWARGS)
    db.init_db(config.db_path)

    application = FastAPI()
    application.state.config = config
    application.add_middleware(SessionMiddleware, secret_key=config.session_secret)
    application.include_router(servers_router)

    @application.exception_handler(NotAuthenticated)
    def _redirect_to_login(request, exc):
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/login", status_code=303)

    return application


@pytest.fixture
def client(app):
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def logged_in_client(app, client):
    app.dependency_overrides[require_session] = lambda: {"email": "a@example.com"}
    return client


def test_servers_page_requires_login(client):
    response = client.get("/servers")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_add_server_creates_record(app, logged_in_client):
    response = logged_in_client.post(
        "/servers",
        data={
            "server_id": "server-a", "host": "10.0.1.10", "port": "22",
            "username": "deploy", "ssh_key_path": "/k.pem",
            "log_path": "/var/log/app.log", "format": "default", "custom_pattern": "",
        },
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/servers"
    server = db.get_server(app.state.config.db_path, "server-a")
    assert server["host"] == "10.0.1.10"


def test_add_server_with_invalid_custom_pattern_shows_error(app, logged_in_client):
    response = logged_in_client.post(
        "/servers",
        data={
            "server_id": "server-a", "host": "h", "port": "22",
            "username": "u", "ssh_key_path": "k", "log_path": "l",
            "format": "custom", "custom_pattern": "(?P<timestamp>[",
        },
    )

    assert response.status_code == 200
    assert "invalid custom_pattern regex" in response.text
    assert db.get_server(app.state.config.db_path, "server-a") is None


def test_add_server_with_duplicate_server_id_shows_error(app, logged_in_client):
    config = app.state.config
    db.insert_server(config.db_path, "server-a", "10.0.1.10", 22, "deploy", "/k.pem", "/var/log/a.log", "default", None)

    response = logged_in_client.post(
        "/servers",
        data={
            "server_id": "server-a", "host": "10.0.1.99", "port": "2222",
            "username": "ops", "ssh_key_path": "/other.pem",
            "log_path": "/var/log/other.log", "format": "default", "custom_pattern": "",
        },
    )

    assert response.status_code == 200
    assert "이미 존재합니다" in response.text

    server = db.get_server(config.db_path, "server-a")
    assert server["host"] == "10.0.1.10"
    assert server["port"] == 22
    assert server["username"] == "deploy"


def test_edit_nonexistent_server_page_redirects(logged_in_client):
    response = logged_in_client.get("/servers/does-not-exist/edit")

    assert response.status_code == 303
    assert response.headers["location"] == "/servers"


def test_edit_nonexistent_server_post_redirects_without_writing(app, logged_in_client):
    config = app.state.config

    with patch("dashboard.routes.servers_routes.db.update_server") as mock_update:
        response = logged_in_client.post(
            "/servers/does-not-exist/edit",
            data={
                "host": "10.0.1.99", "port": "2222", "username": "ops",
                "ssh_key_path": "/new.pem", "log_path": "/var/log/new.log",
                "format": "default", "custom_pattern": "",
            },
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/servers"
    assert db.get_server(config.db_path, "does-not-exist") is None
    mock_update.assert_not_called()


def test_edit_server_updates_record(app, logged_in_client):
    config = app.state.config
    db.insert_server(config.db_path, "server-a", "10.0.1.10", 22, "deploy", "/k.pem", "/var/log/a.log", "default", None)

    response = logged_in_client.post(
        "/servers/server-a/edit",
        data={
            "host": "10.0.1.99", "port": "2222", "username": "ops",
            "ssh_key_path": "/new.pem", "log_path": "/var/log/new.log",
            "format": "default", "custom_pattern": "",
        },
    )

    assert response.status_code == 303
    updated = db.get_server(config.db_path, "server-a")
    assert updated["host"] == "10.0.1.99"
    assert updated["port"] == 2222


def test_delete_server_removes_record(app, logged_in_client):
    config = app.state.config
    db.insert_server(config.db_path, "server-a", "h", 22, "u", "k", "l", "default", None)

    response = logged_in_client.post("/servers/server-a/delete")

    assert response.status_code == 303
    assert db.get_server(config.db_path, "server-a") is None
