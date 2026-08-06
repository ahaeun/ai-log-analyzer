from unittest.mock import MagicMock, patch

from watcher.main import WatcherManager
from watcher.models import ServerEntry, WatcherConfig

CONFIG = WatcherConfig(
    registry_url="http://dashboard/api/servers",
    analyzer_endpoint="https://collector.example.com/api/errors",
    api_key_env="WATCHER_API_KEY",
    registry_poll_interval=30,
    log_poll_interval=15,
)

ENTRY_A = ServerEntry(
    server_id="server-a",
    host="10.0.1.10",
    port=22,
    username="deploy",
    ssh_key_path="/home/watcher/.ssh/server-a.pem",
    log_path="/var/log/app/application.log",
    format="default",
)

ENTRY_B = ServerEntry(
    server_id="server-b",
    host="10.0.1.11",
    port=22,
    username="deploy",
    ssh_key_path="/home/watcher/.ssh/server-b.pem",
    log_path="/var/log/app/application.log",
    format="default",
)


def test_sync_registry_adds_new_servers():
    sender = MagicMock()
    manager = WatcherManager(CONFIG, sender)

    with patch("watcher.main.fetch_servers", return_value=([ENTRY_A], [])):
        manager.sync_registry()

    assert "server-a" in manager._active


def test_sync_registry_removes_deregistered_servers():
    sender = MagicMock()
    manager = WatcherManager(CONFIG, sender)

    with patch("watcher.main.fetch_servers", return_value=([ENTRY_A, ENTRY_B], [])):
        manager.sync_registry()
    assert set(manager._active.keys()) == {"server-a", "server-b"}

    with patch("watcher.main.fetch_servers", return_value=([ENTRY_A], [])):
        manager.sync_registry()

    assert set(manager._active.keys()) == {"server-a"}


def test_sync_registry_keeps_existing_tailer_for_still_registered_server():
    sender = MagicMock()
    manager = WatcherManager(CONFIG, sender)

    with patch("watcher.main.fetch_servers", return_value=([ENTRY_A], [])):
        manager.sync_registry()
    tailer_before, _ = manager._active["server-a"]

    with patch("watcher.main.fetch_servers", return_value=([ENTRY_A], [])):
        manager.sync_registry()
    tailer_after, _ = manager._active["server-a"]

    assert tailer_before is tailer_after


def test_sync_registry_unreachable_keeps_last_known_list():
    sender = MagicMock()
    manager = WatcherManager(CONFIG, sender)

    with patch("watcher.main.fetch_servers", return_value=([ENTRY_A], [])):
        manager.sync_registry()

    with patch("watcher.main.fetch_servers", side_effect=Exception("registry down")):
        manager.sync_registry()

    assert "server-a" in manager._active


def test_poll_once_feeds_lines_and_sends_completed_events():
    sender = MagicMock()
    manager = WatcherManager(CONFIG, sender)

    with patch("watcher.main.fetch_servers", return_value=([ENTRY_A], [])):
        manager.sync_registry()

    tailer, _accumulator = manager._active["server-a"]
    tailer.read_new_bytes = MagicMock(
        return_value=(
            "2026-08-06 12:35:01.123 ERROR 12345 --- [main] com.example.demo.MyService : boom\n"
            "java.lang.RuntimeException: boom\n"
            "2026-08-06 12:35:05.001  INFO 12345 --- [main] com.example.demo.App : Recovered"
        )
    )

    manager.poll_once()

    sender.send.assert_called_once()
    sent_event = sender.send.call_args[0][0]
    assert sent_event.error_type == "java.lang.RuntimeException"


def test_poll_once_skips_server_with_no_new_bytes():
    sender = MagicMock()
    manager = WatcherManager(CONFIG, sender)

    with patch("watcher.main.fetch_servers", return_value=([ENTRY_A], [])):
        manager.sync_registry()

    tailer, _accumulator = manager._active["server-a"]
    tailer.read_new_bytes = MagicMock(return_value="")

    manager.poll_once()

    sender.send.assert_not_called()
