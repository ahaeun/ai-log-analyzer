import secrets
from urllib.parse import urlencode

import requests
from fastapi import Request

SLACK_AUTHORIZE_URL = "https://slack.com/openid/connect/authorize"
SLACK_TOKEN_URL = "https://slack.com/api/openid.connect.token"
SLACK_USERINFO_URL = "https://slack.com/api/openid.connect.userInfo"


class NotAuthenticated(Exception):
    pass


def generate_state():
    return secrets.token_urlsafe(24)


def build_authorize_url(config, redirect_uri, state):
    params = {
        "client_id": config.slack_client_id,
        "scope": "openid email profile",
        "redirect_uri": redirect_uri,
        "state": state,
    }
    return f"{SLACK_AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code_for_token(config, code, redirect_uri):
    response = requests.post(
        SLACK_TOKEN_URL,
        data={
            "client_id": config.slack_client_id,
            "client_secret": config.slack_client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        },
        timeout=5,
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("ok", True):
        raise RuntimeError(f"Slack API error: {data.get('error')}")
    return data["access_token"]


def fetch_userinfo(access_token):
    response = requests.get(
        SLACK_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=5,
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("ok", True):
        raise RuntimeError(f"Slack API error: {data.get('error')}")
    return {
        "email": data.get("email"),
        "team_id": data.get("https://slack.com/team_id"),
    }


def is_authorized(userinfo, config):
    if userinfo.get("team_id") != config.slack_team_id:
        return False
    if userinfo.get("email") not in config.allowed_emails:
        return False
    return True


def require_session(request: Request):
    user = request.session.get("user")
    if not user:
        raise NotAuthenticated()
    return user
