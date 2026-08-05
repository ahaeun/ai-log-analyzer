import re
from datetime import datetime

from watcher.models import ErrorEvent

DEFAULT_PATTERN = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\s+"
    r"(?P<level>[A-Z]+)\s+\d+\s+---\s+\[[^\]]*\]\s+"
    r"(?P<logger>\S+)\s*:\s*(?P<message>.*)$"
)

ERROR_TYPE_PATTERN = re.compile(r"([\w.$]+(?:Exception|Error))")


class LogParser:
    def __init__(self, config):
        self.server_id = config.server_id
        self._entry_pattern = (
            DEFAULT_PATTERN if config.format == "default" else re.compile(config.custom_pattern)
        )

    def match_entry(self, line):
        return self._entry_pattern.match(line)

    def is_error_start(self, match):
        if match is None:
            return False
        groupdict = match.groupdict()
        level = groupdict.get("level", "")
        message = groupdict.get("message", "")
        return level == "ERROR" or "Exception" in message or "Caused by:" in message

    def build_event(self, lines):
        raw_log = "\n".join(lines)
        match = self.match_entry(lines[0])
        groupdict = match.groupdict() if match else {}

        type_match = ERROR_TYPE_PATTERN.search(raw_log)

        return ErrorEvent(
            server_id=self.server_id,
            timestamp=self._normalize_timestamp(groupdict.get("timestamp")),
            log_level=groupdict.get("level", "ERROR"),
            error_type=type_match.group(1) if type_match else "UNKNOWN",
            message=groupdict.get("message", lines[0]),
            stack_trace="\n".join(lines[1:]),
            raw_log=raw_log,
        )

    @staticmethod
    def _normalize_timestamp(raw_timestamp):
        if not raw_timestamp:
            return datetime.now().astimezone().isoformat(timespec="seconds")
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
            try:
                naive = datetime.strptime(raw_timestamp, fmt)
                return naive.astimezone().isoformat(timespec="seconds")
            except ValueError:
                continue
        return raw_timestamp


class ErrorEventAccumulator:
    """Groups a stream of log lines into ErrorEvent objects, one line at a time."""

    def __init__(self, parser: LogParser):
        self._parser = parser
        self._pending_lines = None

    def feed_line(self, line):
        match = self._parser.match_entry(line)

        if match is not None:
            completed = self._finalize_pending()
            if self._parser.is_error_start(match):
                self._pending_lines = [line]
            return completed

        if self._pending_lines is not None:
            self._pending_lines.append(line)

        return None

    def flush(self):
        return self._finalize_pending()

    def _finalize_pending(self):
        if self._pending_lines is None:
            return None
        event = self._parser.build_event(self._pending_lines)
        self._pending_lines = None
        return event
