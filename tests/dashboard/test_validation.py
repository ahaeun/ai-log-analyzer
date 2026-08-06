import pytest

from dashboard.validation import ServerValidationError, validate_server_fields


def test_valid_default_format_passes():
    validate_server_fields(
        "server-a", "10.0.1.10", 22, "deploy",
        "/home/w/.ssh/a.pem", "/var/log/app.log", "default", None,
    )


def test_valid_custom_format_passes():
    validate_server_fields(
        "server-a", "10.0.1.10", 22, "deploy", "/k.pem", "/var/log/app.log",
        "custom", r"^(?P<timestamp>\S+) (?P<level>\w+) (?P<message>.*)$",
    )


def test_custom_format_without_pattern_raises():
    with pytest.raises(ServerValidationError, match="custom_pattern is required"):
        validate_server_fields(
            "server-a", "10.0.1.10", 22, "deploy", "/k.pem", "/var/log/app.log",
            "custom", None,
        )


def test_custom_format_invalid_regex_raises():
    with pytest.raises(ServerValidationError, match="invalid custom_pattern regex"):
        validate_server_fields(
            "server-a", "10.0.1.10", 22, "deploy", "/k.pem", "/var/log/app.log",
            "custom", "(?P<timestamp>[",
        )


def test_custom_format_missing_named_groups_raises():
    with pytest.raises(ServerValidationError, match="named groups"):
        validate_server_fields(
            "server-a", "10.0.1.10", 22, "deploy", "/k.pem", "/var/log/app.log",
            "custom", r"(?P<message>.*)",
        )
