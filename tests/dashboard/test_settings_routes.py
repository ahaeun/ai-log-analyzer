import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from dashboard import db
from dashboard.auth import NotAuthenticated, NotAuthorized, require_master
from dashboard.config import DashboardConfig
from dashboard.routes.settings_routes import settings_router

CONFIG_KWARGS = dict(
    slack_client_id="c", slack_client_secret="s", slack_team_id="T1",
    master_email="master@example.com", session_secret="secret", api_key="k",
)


@pytest.fixture
def app(tmp_path):
    config = DashboardConfig(db_path=str(tmp_path / "test.db"), **CONFIG_KWARGS)
    db.init_db(config.db_path)

    application = FastAPI()
    application.state.config = config
    application.add_middleware(SessionMiddleware, secret_key=config.session_secret)
    application.include_router(settings_router)

    @application.exception_handler(NotAuthenticated)
    def _redirect_to_login(request, exc):
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/login", status_code=303)

    @application.exception_handler(NotAuthorized)
    def _forbidden(request, exc):
        from fastapi.responses import HTMLResponse
        return HTMLResponse("<h1>접근 권한이 없습니다</h1>", status_code=403)

    return application


@pytest.fixture
def client(app):
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def master_client(app, client):
    app.dependency_overrides[require_master] = lambda: {"email": "master@example.com", "is_master": True}
    return client


def test_settings_page_requires_login(client):
    response = client.get("/settings/emails")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_add_email_creates_record(app, master_client):
    response = master_client.post("/settings/emails", data={"email": "a@example.com"})

    assert response.status_code == 303
    assert response.headers["location"] == "/settings/emails"
    emails = db.list_allowed_emails(app.state.config.db_path)
    assert [row["email"] for row in emails] == ["a@example.com"]


def test_add_email_with_invalid_format_shows_error(app, master_client):
    response = master_client.post("/settings/emails", data={"email": "not-an-email"})

    assert response.status_code == 200
    assert "올바른 이메일 형식이 아닙니다" in response.text
    assert db.list_allowed_emails(app.state.config.db_path) == []


def test_add_duplicate_email_shows_error(app, master_client):
    config = app.state.config
    db.add_allowed_email(config.db_path, "a@example.com")

    response = master_client.post("/settings/emails", data={"email": "a@example.com"})

    assert response.status_code == 200
    assert "이미 등록되어 있습니다" in response.text


def test_delete_email_removes_record(app, master_client):
    config = app.state.config
    db.add_allowed_email(config.db_path, "a@example.com")

    response = master_client.post("/settings/emails/a@example.com/delete")

    assert response.status_code == 303
    assert db.list_allowed_emails(config.db_path) == []
