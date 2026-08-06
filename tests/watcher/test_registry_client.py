import pytest
import requests
from unittest.mock import patch

from watcher.registry_client import fetch_servers

VALID_ENTRY = {
    "server_id": "server-a",
    "host": "10.0.1.10",
    "port": 22,
    "username": "deploy",
    "ssh_key_path": "/home/watcher/.ssh/server-a.pem",
    "log_path": "/var/log/app/application.log",
    "format": "default",
    "custom_pattern": None,
}


class FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self):
        return self._json_data


def test_fetch_servers_returns_valid_entries():
    with patch("watcher.registry_client.requests.get", return_value=FakeResponse([VALID_ENTRY])):
        servers, skipped = fetch_servers("http://dashboard/api/servers")

    assert len(servers) == 1
    assert servers[0].server_id == "server-a"
    assert skipped == []


def test_fetch_servers_skips_invalid_entry_but_keeps_valid_ones():
    invalid_entry = {**VALID_ENTRY, "server_id": "server-b", "format": "custom", "custom_pattern": None}
    with patch(
        "watcher.registry_client.requests.get",
        return_value=FakeResponse([VALID_ENTRY, invalid_entry]),
    ):
        servers, skipped = fetch_servers("http://dashboard/api/servers")

    assert len(servers) == 1
    assert servers[0].server_id == "server-a"
    assert len(skipped) == 1
    assert skipped[0][0] == "server-b"


def test_fetch_servers_skips_entry_missing_required_field():
    missing_field_entry = {k: v for k, v in VALID_ENTRY.items() if k != "host"}
    missing_field_entry["server_id"] = "server-c"
    with patch(
        "watcher.registry_client.requests.get",
        return_value=FakeResponse([missing_field_entry]),
    ):
        servers, skipped = fetch_servers("http://dashboard/api/servers")

    assert servers == []
    assert skipped[0][0] == "server-c"


def test_fetch_servers_propagates_registry_unreachable():
    with patch("watcher.registry_client.requests.get", side_effect=requests.ConnectionError):
        with pytest.raises(requests.ConnectionError):
            fetch_servers("http://dashboard/api/servers")


def test_fetch_servers_skips_entry_with_invalid_custom_pattern_regex():
    invalid_regex_entry = {
        **VALID_ENTRY,
        "server_id": "server-d",
        "format": "custom",
        "custom_pattern": "(?P<timestamp>[",
    }
    with patch(
        "watcher.registry_client.requests.get",
        return_value=FakeResponse([VALID_ENTRY, invalid_regex_entry]),
    ):
        servers, skipped = fetch_servers("http://dashboard/api/servers")

    assert len(servers) == 1
    assert servers[0].server_id == "server-a"
    assert len(skipped) == 1
    assert skipped[0][0] == "server-d"


def test_fetch_servers_skips_entry_missing_required_named_group():
    missing_group_entry = {
        **VALID_ENTRY,
        "server_id": "server-e",
        "format": "custom",
        "custom_pattern": "(?P<message>.*)",
    }
    with patch(
        "watcher.registry_client.requests.get",
        return_value=FakeResponse([VALID_ENTRY, missing_group_entry]),
    ):
        servers, skipped = fetch_servers("http://dashboard/api/servers")

    assert len(servers) == 1
    assert servers[0].server_id == "server-a"
    assert len(skipped) == 1
    assert skipped[0][0] == "server-e"


def test_fetch_servers_skips_non_dict_entry():
    with patch(
        "watcher.registry_client.requests.get",
        return_value=FakeResponse([VALID_ENTRY, "not-a-dict"]),
    ):
        servers, skipped = fetch_servers("http://dashboard/api/servers")

    assert len(servers) == 1
    assert servers[0].server_id == "server-a"
    assert len(skipped) == 1
    assert skipped[0][0] == "<unknown>"


def test_fetch_servers_returns_empty_when_response_body_is_not_a_list():
    with patch(
        "watcher.registry_client.requests.get",
        return_value=FakeResponse({"error": "not a list"}),
    ):
        servers, skipped = fetch_servers("http://dashboard/api/servers")

    assert servers == []
    assert len(skipped) == 1
    assert skipped[0][0] == "<unknown>"
