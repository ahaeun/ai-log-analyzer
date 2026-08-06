import logging

import paramiko

logger = logging.getLogger(__name__)


class SSHTailer:
    def __init__(self, entry):
        self.entry = entry
        self._client = None
        self._offset = None

    def read_new_bytes(self):
        if self._client is None:
            if not self._connect():
                return ""

        size = self._remote_size()
        if size is None:
            self._disconnect()
            return ""

        if self._offset is None:
            self._offset = size
            return ""

        if size < self._offset:
            self._offset = 0

        if size == self._offset:
            return ""

        raw = self._remote_tail_from(self._offset)
        if raw is None:
            self._disconnect()
            return ""

        last_newline_idx = raw.rfind(b"\n")
        if last_newline_idx == -1:
            return ""  # only a partial line has arrived so far — wait for more

        complete = raw[:last_newline_idx + 1]
        self._offset += len(complete)
        return complete.decode(errors="replace")

    def _connect(self):
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(
                hostname=self.entry.host,
                port=self.entry.port,
                username=self.entry.username,
                key_filename=self.entry.ssh_key_path,
                timeout=5,
            )
            self._client = client
            return True
        except (paramiko.SSHException, OSError, EOFError) as e:
            logger.warning("SSH connect failed for server %s: %s", self.entry.server_id, e)
            self._client = None
            return False

    def _disconnect(self):
        if self._client is not None:
            self._client.close()
        self._client = None

    def _run_command(self, command):
        try:
            _, stdout, _stderr = self._client.exec_command(command, timeout=5)
            exit_status = stdout.channel.recv_exit_status()
            if exit_status != 0:
                return None
            return stdout.read()
        except (paramiko.SSHException, OSError, EOFError) as e:
            logger.warning("SSH command failed for server %s: %s", self.entry.server_id, e)
            return None

    def _remote_size(self):
        output = self._run_command(f"stat -c%s {self.entry.log_path}")
        if output is None:
            return None
        try:
            return int(output.decode().strip())
        except ValueError:
            return None

    def _remote_tail_from(self, offset):
        return self._run_command(f"tail -c +{offset + 1} {self.entry.log_path}")
