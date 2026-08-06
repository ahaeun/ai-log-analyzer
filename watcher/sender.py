import glob
import json
import os
import threading
import time
from enum import Enum

import requests


class DeliveryResult(Enum):
    DELIVERED = "delivered"
    RETRY = "retry"
    FAILED = "failed"


def next_retry_interval(current_interval, queue_still_has_events, base_seconds=30, max_seconds=300):
    if queue_still_has_events:
        return min(current_interval * 2, max_seconds)
    return base_seconds


class EventSender:
    def __init__(self, config):
        self.config = config
        if not os.environ.get(config.api_key_env):
            raise ValueError(
                f"Environment variable {config.api_key_env!r} is not set (required for API key)"
            )
        os.makedirs(config.queue_dir, exist_ok=True)
        self._locks_guard = threading.Lock()
        self._queue_locks = {}

    def _lock_for(self, server_id):
        with self._locks_guard:
            if server_id not in self._queue_locks:
                self._queue_locks[server_id] = threading.Lock()
            return self._queue_locks[server_id]

    def send(self, event):
        result = self._post_event(event.to_dict())
        if result == DeliveryResult.RETRY:
            self._enqueue(event.server_id, event.to_dict())
        return result == DeliveryResult.DELIVERED

    def flush_queue(self):
        has_remaining = False
        for queue_path in sorted(glob.glob(os.path.join(self.config.queue_dir, "*.jsonl"))):
            server_id = os.path.splitext(os.path.basename(queue_path))[0]
            if self._flush_one_queue_file(server_id, queue_path):
                has_remaining = True
        return has_remaining

    def _flush_one_queue_file(self, server_id, queue_path):
        with self._lock_for(server_id):
            with open(queue_path, "r", encoding="utf-8") as f:
                lines = [line for line in f.read().splitlines() if line]

            remaining = []
            for line in lines:
                event_dict = json.loads(line)
                if self._post_event(event_dict) == DeliveryResult.RETRY:
                    remaining.append(line)

            with open(queue_path, "w", encoding="utf-8") as f:
                for line in remaining:
                    f.write(line + "\n")

            return len(remaining) > 0

    def run_retry_loop(self, interval_seconds=30, max_interval_seconds=300, stop_event=None):
        interval = interval_seconds
        while stop_event is None or not stop_event.is_set():
            time.sleep(interval)
            still_has_queue = self.flush_queue()
            interval = next_retry_interval(
                interval, still_has_queue, base_seconds=interval_seconds, max_seconds=max_interval_seconds
            )

    def _post_event(self, event_dict):
        try:
            response = requests.post(
                self.config.analyzer_endpoint,
                json=event_dict,
                headers={"X-API-Key": self._api_key()},
                timeout=5,
            )
        except requests.RequestException:
            return DeliveryResult.RETRY

        if response.status_code < 300:
            return DeliveryResult.DELIVERED
        if response.status_code >= 500:
            return DeliveryResult.RETRY
        return DeliveryResult.FAILED

    def _api_key(self):
        return os.environ.get(self.config.api_key_env, "")

    def _enqueue(self, server_id, event_dict):
        queue_path = os.path.join(self.config.queue_dir, f"{server_id}.jsonl")
        with self._lock_for(server_id):
            with open(queue_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event_dict) + "\n")
