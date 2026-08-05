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
    def __init__(self, config, queue_dir="watcher/.queue"):
        self.config = config
        if not os.environ.get(config.api_key_env):
            raise ValueError(
                f"Environment variable {config.api_key_env!r} is not set (required for API key)"
            )
        os.makedirs(queue_dir, exist_ok=True)
        self.queue_path = os.path.join(queue_dir, f"{config.server_id}.jsonl")
        self._queue_lock = threading.Lock()

    def send(self, event):
        result = self._post_event(event.to_dict())
        if result == DeliveryResult.RETRY:
            self._enqueue(event.to_dict())
        return result == DeliveryResult.DELIVERED

    def flush_queue(self):
        with self._queue_lock:
            if not os.path.exists(self.queue_path):
                return False

            with open(self.queue_path, "r", encoding="utf-8") as f:
                lines = [line for line in f.read().splitlines() if line]

            remaining = []
            for line in lines:
                event_dict = json.loads(line)
                if self._post_event(event_dict) == DeliveryResult.RETRY:
                    remaining.append(line)

            with open(self.queue_path, "w", encoding="utf-8") as f:
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
                self.config.central_endpoint,
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

    def _enqueue(self, event_dict):
        with self._queue_lock:
            with open(self.queue_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event_dict) + "\n")
