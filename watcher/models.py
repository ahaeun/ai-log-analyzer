import re
from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class ServerEntry:
    server_id: str
    host: str
    port: int
    username: str
    ssh_key_path: str
    log_path: str
    format: str
    custom_pattern: Optional[str] = None

    def __post_init__(self):
        if self.format not in ("default", "custom"):
            raise ValueError(
                f"format must be 'default' or 'custom', got {self.format!r}"
            )
        if self.format == "custom":
            if not self.custom_pattern:
                raise ValueError("custom_pattern is required when format is 'custom'")
            try:
                compiled = re.compile(self.custom_pattern)
            except re.error as e:
                raise ValueError(f"invalid custom_pattern regex: {e}") from e
            required_groups = {"timestamp", "level", "message"}
            missing_groups = required_groups - set(compiled.groupindex.keys())
            if missing_groups:
                raise ValueError(
                    f"custom_pattern must define named groups: {sorted(missing_groups)}"
                )


@dataclass
class WatcherConfig:
    registry_url: str
    analyzer_endpoint: str
    api_key_env: str
    registry_api_key_env: str
    queue_dir: str = "watcher/.queue"
    registry_poll_interval: int = 30
    log_poll_interval: int = 15


@dataclass
class ErrorEvent:
    server_id: str
    timestamp: str
    log_level: str
    error_type: str
    message: str
    stack_trace: str
    raw_log: str

    def to_dict(self):
        return asdict(self)
