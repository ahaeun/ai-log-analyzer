import argparse
import os
import threading
import time

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from watcher.config import load_server_config
from watcher.parser import ErrorEventAccumulator, LogParser
from watcher.sender import EventSender


class LogFileHandler(FileSystemEventHandler):
    def __init__(self, log_path, accumulator, sender):
        self.log_path = os.path.abspath(log_path)
        self.accumulator = accumulator
        self.sender = sender
        self._position = self._current_size()

    def on_modified(self, event):
        if os.path.abspath(event.src_path) != self.log_path:
            return
        self._read_new_lines()

    def on_created(self, event):
        if os.path.abspath(event.src_path) != self.log_path:
            return
        self._position = 0
        self._read_new_lines()

    def _current_size(self):
        return os.path.getsize(self.log_path) if os.path.exists(self.log_path) else 0

    def _read_new_lines(self):
        size = self._current_size()
        if size < self._position:
            self._position = 0

        with open(self.log_path, "rb") as f:
            f.seek(self._position)
            chunk = f.read()

        # Only treat text up to the last newline as "complete" — a chunk read
        # while the writer is mid-line (e.g. a multi-line stack trace still
        # being flushed) must not be consumed as if it were a full line, and
        # _position must not advance past bytes we haven't actually processed.
        last_newline_idx = chunk.rfind(b"\n")
        if last_newline_idx == -1:
            # No complete line yet in this chunk; leave _position untouched
            # so the next read re-reads these bytes plus whatever is appended.
            return

        complete_bytes = chunk[: last_newline_idx + 1]
        self._position += len(complete_bytes)
        new_lines = complete_bytes.decode("utf-8", errors="replace").splitlines()

        for line in new_lines:
            completed_event = self.accumulator.feed_line(line)
            if completed_event is not None:
                self.sender.send(completed_event)


def run(config_path):
    config = load_server_config(config_path)
    parser = LogParser(config)
    accumulator = ErrorEventAccumulator(parser)
    sender = EventSender(config)

    handler = LogFileHandler(config.log_path, accumulator, sender)
    observer = Observer()
    observer.schedule(handler, path=os.path.dirname(config.log_path) or ".", recursive=False)
    observer.start()

    stop_event = threading.Event()
    retry_thread = threading.Thread(
        target=sender.run_retry_loop, kwargs={"stop_event": stop_event}, daemon=True
    )
    retry_thread.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop_event.set()
        observer.stop()
    observer.join()


if __name__ == "__main__":
    argparser = argparse.ArgumentParser(description="Run the log-watcher agent for one server.")
    argparser.add_argument("config_path", help="Path to the server's YAML config file")
    args = argparser.parse_args()
    run(args.config_path)
