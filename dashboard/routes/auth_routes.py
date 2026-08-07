from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from dashboard import auth, db
from dashboard.templating import templates

auth_router = APIRouter()


@auth_router.get("/login")
def login_page(request: Request, error: str = None):
    return templates.TemplateResponse(request, "login.html", {"error": error})


@auth_router.get("/login/slack")
def login_slack(request: Request):
    config = request.app.state.config
    state = auth.generate_state()
    request.session["oauth_state"] = state
    redirect_uri = str(request.url_for("auth_callback"))
    url = auth.build_authorize_url(config, redirect_uri, state)
    return RedirectResponse(url)


@auth_router.get("/auth/slack/callback", name="auth_callback")
def auth_callback(request: Request, code: str = None, state: str = None):
    config = request.app.state.config
    expected_state = request.session.pop("oauth_state", None)
    if not code or not state or state != expected_state:
        return RedirectResponse("/login?error=state", status_code=303)

    redirect_uri = str(request.url_for("auth_callback"))
    try:
        access_token = auth.exchange_code_for_token(config, code, redirect_uri)
        userinfo = auth.fetch_userinfo(access_token)
    except Exception:
        return RedirectResponse("/login?error=slack", status_code=303)

    allowed_emails = [row["email"] for row in db.list_allowed_emails(config.db_path)]
    if not auth.is_authorized(userinfo, config, allowed_emails):
        return HTMLResponse("<h1>접근 권한이 없습니다</h1>", status_code=403)

    request.session["user"] = {
        "email": userinfo["email"],
        "is_master": userinfo["email"] == config.master_email,
    }
    return RedirectResponse("/", status_code=303)


@auth_router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
