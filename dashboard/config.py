import os
from dataclasses import dataclass

REQUIRED_ENV_VARS = (
    "SLACK_CLIENT_ID",
    "SLACK_CLIENT_SECRET",
    "SLACK_TEAM_ID",
    "DASHBOARD_MASTER_EMAIL",
    "DASHBOARD_SESSION_SECRET",
    "DASHBOARD_API_KEY",
)


@dataclass
class DashboardConfig:
    db_path: str
    slack_client_id: str
    slack_client_secret: str
    slack_team_id: str
    master_email: str
    session_secret: str
    api_key: str


def load_config_from_env() -> DashboardConfig:
    missing = [name for name in REQUIRED_ENV_VARS if not os.environ.get(name)]
    if missing:
        raise ValueError(f"Missing required environment variables: {missing}")

    return DashboardConfig(
        db_path=os.environ.get("DASHBOARD_DB_PATH", "dashboard/data.db"),
        slack_client_id=os.environ["SLACK_CLIENT_ID"],
        slack_client_secret=os.environ["SLACK_CLIENT_SECRET"],
        slack_team_id=os.environ["SLACK_TEAM_ID"],
        master_email=os.environ["DASHBOARD_MASTER_EMAIL"],
        session_secret=os.environ["DASHBOARD_SESSION_SECRET"],
        api_key=os.environ["DASHBOARD_API_KEY"],
    )
