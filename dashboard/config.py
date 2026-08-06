import os
from dataclasses import dataclass
from typing import List

REQUIRED_ENV_VARS = (
    "SLACK_CLIENT_ID",
    "SLACK_CLIENT_SECRET",
    "SLACK_TEAM_ID",
    "DASHBOARD_ALLOWED_EMAILS",
    "DASHBOARD_SESSION_SECRET",
    "DASHBOARD_API_KEY",
)


@dataclass
class DashboardConfig:
    db_path: str
    slack_client_id: str
    slack_client_secret: str
    slack_team_id: str
    allowed_emails: List[str]
    session_secret: str
    api_key: str


def load_config_from_env() -> DashboardConfig:
    missing = [name for name in REQUIRED_ENV_VARS if not os.environ.get(name)]
    if missing:
        raise ValueError(f"Missing required environment variables: {missing}")

    allowed_emails = [
        email.strip()
        for email in os.environ["DASHBOARD_ALLOWED_EMAILS"].split(",")
        if email.strip()
    ]

    return DashboardConfig(
        db_path=os.environ.get("DASHBOARD_DB_PATH", "dashboard/data.db"),
        slack_client_id=os.environ["SLACK_CLIENT_ID"],
        slack_client_secret=os.environ["SLACK_CLIENT_SECRET"],
        slack_team_id=os.environ["SLACK_TEAM_ID"],
        allowed_emails=allowed_emails,
        session_secret=os.environ["DASHBOARD_SESSION_SECRET"],
        api_key=os.environ["DASHBOARD_API_KEY"],
    )
