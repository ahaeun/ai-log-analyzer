from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class ServerConfig:
    server_id: str
    log_path: str
    format: str
    central_endpoint: str
    api_key_env: str
    custom_pattern: Optional[str] = None

    def __post_init__(self):
        if self.format not in ("default", "custom"):
            raise ValueError(
                f"format must be 'default' or 'custom', got {self.format!r}"
            )
        if self.format == "custom" and not self.custom_pattern:
            raise ValueError("custom_pattern is required when format is 'custom'")


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
