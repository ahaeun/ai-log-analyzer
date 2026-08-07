import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from dashboard import db
from dashboard.auth import NotAuthenticated, require_session
from dashboard.config import DashboardConfig
from dashboard.routes.home_routes import home_router

CONFIG = DashboardConfig(
    db_path="", slack_client_id="c", slack_client_secret="s", slack_team_id="T1",
    allowed_emails=["a@example.com"], session_secret="secret", api_key="k",
)


@pytest.fixture
def app(tmp_path):
    config = DashboardConfig(**{**CONFIG.__dict__, "db_path": str(tmp_path / "test.db")})
    db.init_db(config.db_path)

    application = FastAPI()
    application.state.config = config
    application.add_middleware(SessionMiddleware, secret_key=config.session_secret)
    application.include_router(home_router)

    @application.exception_handler(NotAuthenticated)
    def _redirect_to_login(request, exc):
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/login", status_code=303)

    return application


@pytest.fixture
def client(app):
    return TestClient(app, follow_redirects=False)


def test_home_redirects_when_not_logged_in(client):
    response = client.get("/")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_home_shows_stats_when_logged_in(app, client):
    config = app.state.config
    db.insert_server(config.db_path, "server-a", "h", 22, "u", "k", "l", "default", None)
    db.insert_error(
        config.db_path, "server-a", "2026-08-06T10:00:00+09:00", "ERROR",
        "java.lang.NullPointerException", "boom", "at ...", "raw",
        notified=True, notified_at="2026-08-06T10:00:05+09:00",
    )
    app.dependency_overrides[require_session] = lambda: {"email": "a@example.com"}

    response = client.get("/")

    assert response.status_code == 200
    assert "1" in response.text  # 서버 1대
