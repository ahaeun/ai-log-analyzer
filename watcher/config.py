import yaml

from watcher.models import WatcherConfig

REQUIRED_FIELDS = ("registry_url", "analyzer_endpoint", "api_key_env", "registry_api_key_env")
OPTIONAL_FIELDS = ("queue_dir", "registry_poll_interval", "log_poll_interval")


def load_watcher_config(path: str) -> WatcherConfig:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    missing = [field for field in REQUIRED_FIELDS if field not in data]
    if missing:
        raise ValueError(f"Missing required config fields: {missing}")

    kwargs = {field: data[field] for field in REQUIRED_FIELDS}
    for field in OPTIONAL_FIELDS:
        if field in data:
            kwargs[field] = data[field]

    return WatcherConfig(**kwargs)
