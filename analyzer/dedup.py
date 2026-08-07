import hashlib

import redis as redis_lib

DEDUP_TTL_SECONDS = 600


def _dedup_key(event):
    raw = f"{event.server_id}:{event.error_type}:{event.message}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"analyzer:dedup:{digest}"


def is_duplicate(redis_url, event):
    key = _dedup_key(event)
    try:
        client = redis_lib.Redis.from_url(redis_url)
        was_set = client.set(key, "1", ex=DEDUP_TTL_SECONDS, nx=True)
    except Exception:
        return False
    return not was_set
