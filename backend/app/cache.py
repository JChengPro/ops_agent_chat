"""Optional shared computation cache. Authorization and execution state stay in PostgreSQL."""
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import hashlib
import json
import logging
import time
from threading import Lock

import redis

from app.core.config import get_settings

logger = logging.getLogger(__name__)
_retry_after = 0.0
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="shared-cache")
_lock = Lock()
_pending = None


def _bounded_io(operation):
    global _pending
    # Socket timeouts exclude DNS. Permit one pending operation, with no growing work queue.
    with _lock:
        if _pending is not None and not _pending.done():
            return None
        future = _pool.submit(operation)
        _pending = future
    return future.result(timeout=0.2)


def cache_key(namespace, value):
    digest = hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()
    return f"ops:v1:{namespace}:{digest}"


@lru_cache(maxsize=4)
def _client(url):
    return redis.Redis.from_url(url, socket_connect_timeout=0.15, socket_timeout=0.15,
                                max_connections=8, decode_responses=True)


def _failure(exc):
    global _retry_after
    _retry_after = time.monotonic() + 5
    logger.warning("Shared cache unavailable (%s); using normal computation", type(exc).__name__)


def get_json(key):
    url = get_settings().redis_url
    if not url or time.monotonic() < _retry_after:
        return None
    try:
        raw = _bounded_io(lambda: _client(url).get(key))
        return json.loads(raw) if raw else None
    except (redis.RedisError, ValueError, TypeError, FutureTimeoutError) as exc:
        _failure(exc)
        return None


def set_json(key, value, ttl):
    url = get_settings().redis_url
    if not url or ttl <= 0 or time.monotonic() < _retry_after:
        return
    try:
        encoded = json.dumps(value, allow_nan=False, separators=(",", ":"))
        _bounded_io(lambda: _client(url).set(key, encoded, ex=ttl))
    except (redis.RedisError, ValueError, TypeError, FutureTimeoutError) as exc:
        _failure(exc)
