import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from dashboard import db
from dashboard.auth import NotAuthenticated, require_session
from dashboard.config import DashboardConfig
from dashboard.routes.errors_routes import errors_router

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
    application.include_router(errors_router)

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


def test_errors_page_requires_login(client):
    response = client.get("/errors")
    assert response.status_code == 303


def test_errors_page_lists_all_by_default(app, logged_in_client):
    config = app.state.config
    db.insert_error(config.db_path, "server-a", "2026-08-06T10:00:00+09:00", "ERROR",
                     "java.lang.NullPointerException", "boom", "at ...", "raw")

    response = logged_in_client.get("/errors")

    assert response.status_code == 200
    assert "java.lang.NullPointerException" in response.text


def test_errors_page_filters_by_server_id(app, logged_in_client):
    config = app.state.config
    db.insert_error(config.db_path, "server-a", "2026-08-06T10:00:00+09:00", "ERROR", "Type-A", "m", "s", "r")
    db.insert_error(config.db_path, "server-b", "2026-08-06T10:00:00+09:00", "ERROR", "Type-B", "m", "s", "r")

    response = logged_in_client.get("/errors", params={"server_id": "server-b"})

    assert "Type-B" in response.text
    assert "Type-A" not in response.text


def test_errors_page_paginates_results(app, logged_in_client):
    config = app.state.config
    for i in range(60):
        db.insert_error(
            config.db_path, "server-a",
            f"2026-08-06T{i:02d}:00:00+09:00", "ERROR", f"Type-{i}", "m", "s", "r",
        )

    page1 = logged_in_client.get("/errors")
    assert page1.status_code == 200
    assert ">다음</a>" in page1.text

    page2 = logged_in_client.get("/errors", params={"page": 2})
    assert page2.status_code == 200
    assert ">다음</a>" not in page2.text
    # page 2 should show the 10 oldest remaining rows (Type-0 .. Type-9)
    assert "Type-0" in page2.text
    assert "Type-59" not in page2.text
