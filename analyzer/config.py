import os
from dataclasses import dataclass

REQUIRED_ENV_VARS = (
    "ANALYZER_API_KEY",
    "OPENAI_API_KEY",
    "REDIS_URL",
    "SLACK_WEBHOOK_URL",
    "DASHBOARD_URL",
    "DASHBOARD_API_KEY",
)


@dataclass
class AnalyzerConfig:
    api_key: str
    openai_api_key: str
    redis_url: str
    slack_webhook_url: str
    dashboard_url: str
    dashboard_api_key: str


def load_config_from_env() -> AnalyzerConfig:
    missing = [name for name in REQUIRED_ENV_VARS if not os.environ.get(name)]
    if missing:
        raise ValueError(f"Missing required environment variables: {missing}")

    return AnalyzerConfig(
        api_key=os.environ["ANALYZER_API_KEY"],
        openai_api_key=os.environ["OPENAI_API_KEY"],
        redis_url=os.environ["REDIS_URL"],
        slack_webhook_url=os.environ["SLACK_WEBHOOK_URL"],
        dashboard_url=os.environ["DASHBOARD_URL"],
        dashboard_api_key=os.environ["DASHBOARD_API_KEY"],
    )
