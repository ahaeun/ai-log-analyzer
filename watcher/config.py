import re

import yaml

from watcher.models import ServerConfig

REQUIRED_FIELDS = ("server_id", "log_path", "format", "central_endpoint", "api_key_env")
REQUIRED_CUSTOM_GROUPS = {"timestamp", "level", "message"}


def load_server_config(path: str) -> ServerConfig:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    missing = [field for field in REQUIRED_FIELDS if field not in data]
    if missing:
        raise ValueError(f"Missing required config fields: {missing}")

    custom_pattern = data.get("custom_pattern")
    if data["format"] == "custom":
        if not custom_pattern:
            raise ValueError("custom_pattern is required when format is 'custom'")
        compiled = re.compile(custom_pattern)
        missing_groups = REQUIRED_CUSTOM_GROUPS - set(compiled.groupindex.keys())
        if missing_groups:
            raise ValueError(
                f"custom_pattern must define named groups: {sorted(missing_groups)}"
            )

    return ServerConfig(
        server_id=data["server_id"],
        log_path=data["log_path"],
        format=data["format"],
        central_endpoint=data["central_endpoint"],
        api_key_env=data["api_key_env"],
        custom_pattern=custom_pattern,
    )
