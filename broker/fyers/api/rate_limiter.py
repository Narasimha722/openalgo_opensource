"""
Shared rate limiting and 429-retry helpers for all Fyers API calls.

Fyers enforces a single global cap per API key across every REST endpoint --
order, data, quotes, depth, history, funds: 10 requests/second, 200/minute,
100000/day (see fyers-api-docs/FYERS_API_v3.md -> "Rate Limits"). Unlike Dhan,
which has independent per-endpoint-class limits (charts vs marketfeed), Fyers'
10 req/sec budget is shared process-wide, so pacing state MUST live in one
place that every module importing it sees -- not per BrokerData instance.

Services create a fresh BrokerData(auth_token) per request (see
services/option_chain_service.py, services/oi_tracker_service.py, etc.), so
any rate-limit state kept on `self` is reset away on every call and never
actually paces anything against concurrent requests. That was the root cause
of option-chain/depth bursts (many individual /data/depth calls for OI)
routinely exceeding the real 10 req/sec cap and getting HTTP 429'd.
"""

import threading
import time
from collections import deque

_lock = threading.Lock()
_last_call_time = 0.0
_minute_calls = deque()
_history_calls = deque()
_day_key = None
_day_calls = 0
MAX_PER_MINUTE = 190
HISTORY_PER_MINUTE = 150
HISTORY_DAILY_BUDGET = 90000

# Documented cap is 10 req/sec; pace at ~8 req/sec (0.125s) to leave headroom
# for clock jitter and for order/fund/margin calls sharing the same quota
# from other modules running concurrently in the same process.
MIN_INTERVAL = 0.125

MAX_RETRIES = 3
BASE_BACKOFF = 1.0  # seconds; exponential fallback when no Retry-After header: 1, 2, 4


def apply_rate_limit(history=False):
    """Block the calling thread until it is safe to make another Fyers API call.

    Recheck dispatch against a shared second/minute budget. History has a
    smaller minute/day allowance to leave room for interactive account calls.
    State is local to this process, so HTTP 429 handling is still required.
    """
    global _last_call_time, _day_key, _day_calls
    while True:
        with _lock:
            now = time.monotonic()
            # Fyers account day in IST. Counters cover this process lifetime;
            # broker responses remain authoritative across restarts/other apps.
            day = int((time.time() + 19800) // 86400)
            if day != _day_key:
                _day_key, _day_calls = day, 0
            if history and _day_calls >= HISTORY_DAILY_BUDGET:
                raise RuntimeError("Fyers history daily budget reached; retry on the next IST day")
            for calls in (_minute_calls, _history_calls):
                while calls and calls[0] <= now - 60:
                    calls.popleft()
            delay = max(0, _last_call_time + MIN_INTERVAL - now)
            if len(_minute_calls) >= MAX_PER_MINUTE:
                delay = max(delay, _minute_calls[0] + 60 - now)
            if history and len(_history_calls) >= HISTORY_PER_MINUTE:
                delay = max(delay, _history_calls[0] + 60 - now)
            if delay <= 0:
                _last_call_time = now
                _minute_calls.append(now)
                if history:
                    _history_calls.append(now)
                _day_calls += 1
                return
        # Recheck at dispatch, so delayed threads cannot wake in a burst.
        time.sleep(min(delay, 1.0))


def retry_delay_from_headers(headers, attempt):
    """Compute how long to wait before retrying a 429.

    Fyers documents both `Retry-After` (seconds) and `X-Retry-After-Ms`
    (milliseconds) response headers on rate-limited requests -- prefer those
    over a blind exponential guess when present.
    """
    retry_after_ms = headers.get("X-Retry-After-Ms") or headers.get("x-retry-after-ms")
    if retry_after_ms:
        try:
            return max(float(retry_after_ms) / 1000.0, 0.05)
        except ValueError:
            pass

    retry_after = headers.get("Retry-After") or headers.get("retry-after")
    if retry_after:
        try:
            return max(float(retry_after), 0.05)
        except ValueError:
            pass

    return BASE_BACKOFF * (2**attempt)
