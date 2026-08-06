import time

import pytest

from dashboard import db


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "test.db")
    db.init_db(path)
    return path


def test_insert_and_get_server(db_path):
    db.insert_server(
        db_path, "server-a", "10.0.1.10", 22, "deploy",
        "/home/w/.ssh/a.pem", "/var/log/app/app.log", "default", None,
    )

    server = db.get_server(db_path, "server-a")

    assert server["server_id"] == "server-a"
    assert server["host"] == "10.0.1.10"
    assert server["port"] == 22
    assert server["format"] == "default"
    assert server["custom_pattern"] is None
    assert server["created_at"]
    assert server["updated_at"]


def test_get_server_returns_none_when_missing(db_path):
    assert db.get_server(db_path, "does-not-exist") is None


def test_list_servers_ordered_by_id(db_path):
    db.insert_server(db_path, "server-b", "h", 22, "u", "k", "l", "default", None)
    db.insert_server(db_path, "server-a", "h", 22, "u", "k", "l", "default", None)

    servers = db.list_servers(db_path)

    assert [s["server_id"] for s in servers] == ["server-a", "server-b"]


def test_update_server_changes_fields_and_updated_at(db_path):
    db.insert_server(db_path, "server-a", "10.0.1.10", 22, "deploy", "k", "l", "default", None)
    original = db.get_server(db_path, "server-a")
    time.sleep(1.1)  # Ensure updated_at timestamp differs (second precision)

    db.update_server(
        db_path, "server-a", "10.0.1.99", 2222, "ops",
        "/new/key.pem", "/var/log/new.log", "custom",
        r"^(?P<timestamp>\S+) (?P<level>\w+) (?P<message>.*)$",
    )
    updated = db.get_server(db_path, "server-a")

    assert updated["host"] == "10.0.1.99"
    assert updated["port"] == 2222
    assert updated["format"] == "custom"
    assert updated["updated_at"] != original["updated_at"]


def test_delete_server_removes_it(db_path):
    db.insert_server(db_path, "server-a", "h", 22, "u", "k", "l", "default", None)

    db.delete_server(db_path, "server-a")

    assert db.get_server(db_path, "server-a") is None


def _insert_sample_error(db_path, server_id="server-a", timestamp="2026-08-06T10:00:00+09:00",
                          error_type="java.lang.NullPointerException", notified=False, notified_at=None):
    db.insert_error(
        db_path, server_id, timestamp, "ERROR", error_type,
        "boom", "at ...", "raw log line",
        ai_analysis="원인: ...", notified=notified, notified_at=notified_at,
    )


def test_insert_error_and_query_all(db_path):
    _insert_sample_error(db_path)

    results = db.query_errors(db_path)

    assert len(results) == 1
    assert results[0]["server_id"] == "server-a"
    assert results[0]["error_type"] == "java.lang.NullPointerException"
    assert results[0]["ai_analysis"] == "원인: ..."
    assert results[0]["notified"] == 0
    assert results[0]["received_at"]


def test_query_errors_filters_by_server_id(db_path):
    _insert_sample_error(db_path, server_id="server-a")
    _insert_sample_error(db_path, server_id="server-b")

    results = db.query_errors(db_path, server_id="server-b")

    assert len(results) == 1
    assert results[0]["server_id"] == "server-b"


def test_query_errors_filters_by_date_range(db_path):
    _insert_sample_error(db_path, timestamp="2026-08-01T10:00:00+09:00")
    _insert_sample_error(db_path, timestamp="2026-08-10T10:00:00+09:00")

    results = db.query_errors(db_path, date_from="2026-08-05T00:00:00+09:00")

    assert len(results) == 1
    assert results[0]["timestamp"] == "2026-08-10T10:00:00+09:00"


def test_query_errors_filters_by_error_type_substring(db_path):
    _insert_sample_error(db_path, error_type="java.lang.NullPointerException")
    _insert_sample_error(db_path, error_type="java.lang.RuntimeException")

    results = db.query_errors(db_path, error_type="Null")

    assert len(results) == 1
    assert results[0]["error_type"] == "java.lang.NullPointerException"


def test_query_errors_respects_limit_and_offset(db_path):
    for i in range(3):
        _insert_sample_error(db_path, timestamp=f"2026-08-0{i+1}T10:00:00+09:00")

    page = db.query_errors(db_path, limit=1, offset=1)

    assert len(page) == 1
    assert page[0]["timestamp"] == "2026-08-02T10:00:00+09:00"


def test_count_servers(db_path):
    db.insert_server(db_path, "server-a", "h", 22, "u", "k", "l", "default", None)
    db.insert_server(db_path, "server-b", "h", 22, "u", "k", "l", "default", None)

    assert db.count_servers(db_path) == 2


def test_count_errors_since(db_path):
    _insert_sample_error(db_path, timestamp="2026-08-01T10:00:00+09:00")
    _insert_sample_error(db_path, timestamp="2026-08-10T10:00:00+09:00")

    assert db.count_errors_since(db_path, "2026-08-05T00:00:00+09:00") == 1


def test_count_notified_since(db_path):
    _insert_sample_error(db_path, notified=True, notified_at="2026-08-10T10:00:05+09:00")
    _insert_sample_error(db_path, notified=False)

    assert db.count_notified_since(db_path, "2026-08-05T00:00:00+09:00") == 1


def test_error_counts_by_day_groups_correctly(db_path):
    _insert_sample_error(db_path, timestamp="2026-08-06T09:00:00+09:00")
    _insert_sample_error(db_path, timestamp="2026-08-06T15:00:00+09:00")
    _insert_sample_error(db_path, timestamp="2026-08-07T09:00:00+09:00")

    counts = db.error_counts_by_day(db_path, "2026-08-01T00:00:00+09:00")

    assert counts == [{"day": "2026-08-06", "count": 2}, {"day": "2026-08-07", "count": 1}]


def test_recent_errors_orders_newest_first_and_limits(db_path):
    _insert_sample_error(db_path, timestamp="2026-08-01T10:00:00+09:00")
    _insert_sample_error(db_path, timestamp="2026-08-10T10:00:00+09:00")

    results = db.recent_errors(db_path, limit=1)

    assert len(results) == 1
    assert results[0]["timestamp"] == "2026-08-10T10:00:00+09:00"
