import argparse
import logging
import threading
import time

from watcher.config import load_watcher_config
from watcher.parser import ErrorEventAccumulator, LogParser
from watcher.registry_client import fetch_servers
from watcher.sender import EventSender
from watcher.ssh_tail import SSHTailer

logger = logging.getLogger(__name__)


class WatcherManager:
    def __init__(self, config, sender):
        self.config = config
        self.sender = sender
        self._active = {}
        self._lock = threading.Lock()

    def sync_registry(self):
        try:
            servers, skipped = fetch_servers(self.config.registry_url)
        except Exception as e:
            logger.warning("registry fetch failed, keeping last known server list: %s", e)
            return

        for server_id, reason in skipped:
            logger.warning("skipping invalid server entry %s: %s", server_id, reason)

        current_ids = {entry.server_id for entry in servers}

        with self._lock:
            for server_id in list(self._active.keys()):
                if server_id not in current_ids:
                    tailer, _accumulator = self._active[server_id]
                    tailer._disconnect()
                    del self._active[server_id]

            for entry in servers:
                if entry.server_id not in self._active:
                    parser = LogParser(entry)
                    accumulator = ErrorEventAccumulator(parser)
                    tailer = SSHTailer(entry)
                    self._active[entry.server_id] = (tailer, accumulator)

    def poll_once(self):
        with self._lock:
            items = list(self._active.values())

        for tailer, accumulator in items:
            text = tailer.read_new_bytes()
            if not text:
                continue
            for line in text.splitlines():
                completed_event = accumulator.feed_line(line)
                if completed_event is not None:
                    self.sender.send(completed_event)

    def run(self, stop_event):
        def registry_loop():
            while not stop_event.is_set():
                self.sync_registry()
                stop_event.wait(self.config.registry_poll_interval)

        def poll_loop():
            while not stop_event.is_set():
                self.poll_once()
                stop_event.wait(self.config.log_poll_interval)

        registry_thread = threading.Thread(target=registry_loop, daemon=True)
        poll_thread = threading.Thread(target=poll_loop, daemon=True)
        retry_thread = threading.Thread(
            target=self.sender.run_retry_loop, kwargs={"stop_event": stop_event}, daemon=True
        )

        registry_thread.start()
        poll_thread.start()
        retry_thread.start()

        try:
            while not stop_event.is_set():
                time.sleep(1)
        except KeyboardInterrupt:
            stop_event.set()


def run(config_path):
    config = load_watcher_config(config_path)
    sender = EventSender(config)
    manager = WatcherManager(config, sender)
    stop_event = threading.Event()
    manager.run(stop_event)


if __name__ == "__main__":
    argparser = argparse.ArgumentParser(description="Run the central SSH-based log watcher.")
    argparser.add_argument("config_path", help="Path to the watcher's YAML config file")
    args = argparser.parse_args()
    run(args.config_path)
