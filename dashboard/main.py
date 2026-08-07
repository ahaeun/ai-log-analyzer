from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from fastapi.responses import HTMLResponse

from dashboard import db
from dashboard.auth import NotAuthenticated, NotAuthorized
from dashboard.config import load_config_from_env
from dashboard.routes.api import api_router
from dashboard.routes.auth_routes import auth_router
from dashboard.routes.errors_routes import errors_router
from dashboard.routes.home_routes import home_router
from dashboard.routes.servers_routes import servers_router
from dashboard.routes.settings_routes import settings_router


def create_app(config):
    db.init_db(config.db_path)

    app = FastAPI()
    app.state.config = config
    app.add_middleware(SessionMiddleware, secret_key=config.session_secret)

    app.include_router(auth_router)
    app.include_router(home_router)
    app.include_router(servers_router)
    app.include_router(errors_router)
    app.include_router(api_router)
    app.include_router(settings_router)

    @app.exception_handler(NotAuthenticated)
    def _redirect_to_login(request, exc):
        return RedirectResponse("/login", status_code=303)

    @app.exception_handler(NotAuthorized)
    def _forbidden(request, exc):
        return HTMLResponse("<h1>접근 권한이 없습니다</h1>", status_code=403)

    return app


def app_factory():
    """실제 서버 구동 시 환경변수를 읽어 앱을 만든다.
    실행: uvicorn dashboard.main:app_factory --factory
    """
    return create_app(load_config_from_env())
