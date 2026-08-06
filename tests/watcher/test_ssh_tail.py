from unittest.mock import MagicMock, patch

from watcher.models import ServerEntry
from watcher.ssh_tail import SSHTailer

ENTRY = ServerEntry(
    server_id="server-a",
    host="10.0.1.10",
    port=22,
    username="deploy",
    ssh_key_path="/home/watcher/.ssh/server-a.pem",
    log_path="/var/log/app/application.log",
    format="default",
)


def test_first_read_sets_offset_to_current_size_without_returning_history():
    client = MagicMock()

    def exec_command(command, timeout=None):
        stdout = MagicMock()
        assert "stat -c%s" in command
        stdout.channel.recv_exit_status.return_value = 0
        stdout.read.return_value = b"100\n"
        return (MagicMock(), stdout, MagicMock())

    client.exec_command.side_effect = exec_command

    with patch("watcher.ssh_tail.paramiko.SSHClient", return_value=client):
        tailer = SSHTailer(ENTRY)
        result = tailer.read_new_bytes()

    assert result == ""
    assert tailer._offset == 100


def test_second_read_returns_only_new_bytes():
    call_count = {"n": 0}
    client = MagicMock()

    def exec_command(command, timeout=None):
        stdout = MagicMock()
        if "stat -c%s" in command:
            call_count["n"] += 1
            size = b"100\n" if call_count["n"] == 1 else b"120\n"
            stdout.channel.recv_exit_status.return_value = 0
            stdout.read.return_value = size
        elif "tail -c" in command:
            assert command.strip().startswith("tail -c +101")
            stdout.channel.recv_exit_status.return_value = 0
            stdout.read.return_value = b"new error line\n"
        return (MagicMock(), stdout, MagicMock())

    client.exec_command.side_effect = exec_command

    with patch("watcher.ssh_tail.paramiko.SSHClient", return_value=client):
        tailer = SSHTailer(ENTRY)
        first = tailer.read_new_bytes()
        second = tailer.read_new_bytes()

    assert first == ""
    assert second == "new error line\n"
    assert tailer._offset == 120


def test_connection_failure_returns_empty_and_retries_next_call():
    with patch("watcher.ssh_tail.paramiko.SSHClient") as mock_ssh_client_cls:
        mock_ssh_client_cls.return_value.connect.side_effect = OSError("unreachable")
        tailer = SSHTailer(ENTRY)
        result = tailer.read_new_bytes()

    assert result == ""
    assert tailer._client is None


def test_rotation_resets_offset_and_rereads_from_start():
    call_count = {"n": 0}
    client = MagicMock()

    def exec_command(command, timeout=None):
        stdout = MagicMock()
        if "stat -c%s" in command:
            call_count["n"] += 1
            size = b"100\n" if call_count["n"] == 1 else b"20\n"
            stdout.channel.recv_exit_status.return_value = 0
            stdout.read.return_value = size
        elif "tail -c" in command:
            assert command.strip().startswith("tail -c +1 ")
            stdout.channel.recv_exit_status.return_value = 0
            stdout.read.return_value = b"after rotation\n"
        return (MagicMock(), stdout, MagicMock())

    client.exec_command.side_effect = exec_command

    with patch("watcher.ssh_tail.paramiko.SSHClient", return_value=client):
        tailer = SSHTailer(ENTRY)
        tailer.read_new_bytes()
        result = tailer.read_new_bytes()

    assert result == "after rotation\n"
    assert tailer._offset == 20
