"""Bounded concurrent Fyers backfill with atomic per-window checkpoints."""

import os
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from database.historify_coverage import ensure_coverage_table, get_coverage, save_coverage
from database.historify_db import get_connection, upsert_market_data
from utils.httpx_client import get_httpx_client
from utils.logging import get_logger

logger = get_logger(__name__)
_workers = max(1, min(4, int(os.getenv("HISTORIFY_FETCH_WORKERS", "3"))))
_executor = ThreadPoolExecutor(max_workers=_workers, thread_name_prefix="historify-fetch")
_writer_lock = threading.Lock()


class DownloadInterrupted(Exception):
    """Cancellation leaves all committed windows available for resume."""


def missing_windows(start, end, covered, chunk_days):
    """Subtract confirmed intervals, then split gaps into inclusive windows."""
    cursor = start
    for left, right in sorted(covered):
        if right < cursor:
            continue
        if left > end:
            break
        while cursor < left and cursor <= end:
            stop = min(left - timedelta(days=1), end, cursor + timedelta(days=chunk_days - 1))
            yield cursor, stop
            cursor = stop + timedelta(days=1)
        cursor = max(cursor, right + timedelta(days=1))
    while cursor <= end:
        stop = min(end, cursor + timedelta(days=chunk_days - 1))
        yield cursor, stop
        cursor = stop + timedelta(days=1)


def download_fyers(
    symbol, exchange, interval, start_date, end_date, auth_token, *, resume=True, checkpoint=None
):
    """Fetch at most a few windows ahead; persist in chronological order.

    The worker pool only performs broker I/O. One caller writes a bounded
    DataFrame and its coverage in the same DuckDB transaction. A failed
    request never advances coverage, even if later requests already finished.
    """
    from broker.fyers.api.data import BrokerData

    if interval not in ("1m", "D"):
        raise ValueError("Historify downloads support 1m and D")
    start = date.fromisoformat(str(start_date))
    requested_end = date.fromisoformat(str(end_date))
    if start > requested_end:
        raise ValueError("Start date cannot be after end date")
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    end = min(requested_end, today)
    if start > end:
        raise ValueError("Start date cannot be in the future")
    symbol, exchange = symbol.upper(), exchange.upper()
    with _writer_lock, get_connection() as conn:
        ensure_coverage_table(conn)
        covered = get_coverage(conn, "fyers", symbol, exchange, interval) if resume else []
        # Initialize the shared connection pool before workers race to use it.
        get_httpx_client()
    # Always refresh today and yesterday; neither is immutable.
    stable_end = today - timedelta(days=2)
    covered = [(left, min(right, stable_end)) for left, right in covered if left <= stable_end]
    windows = iter(missing_windows(start, end, covered, 366 if interval == "D" else 100))
    handler = BrokerData(auth_token)
    pending = deque()
    records = 0

    def check():
        if checkpoint:
            checkpoint()

    def fetch(left, right):
        check()
        from database.symbol import db_session as symbol_session

        try:
            return handler.get_history(symbol, exchange, interval, str(left), str(right))
        finally:
            # Cache misses in broker-symbol resolution use a scoped session.
            # Fetch workers have no Flask teardown handler.
            symbol_session.remove()

    def submit_next():
        check()
        window = next(windows, None)
        if window is not None:
            left, right = window
            pending.append((left, right, _executor.submit(fetch, left, right)))

    try:
        for _ in range(_workers):
            submit_next()
        while pending:
            left, right, future = pending[0]
            # Poll cooperatively in both standard-thread and eventlet runtimes.
            while not future.done():
                check()
                time.sleep(0.1)
            check()
            frame = future.result()
            with _writer_lock, get_connection() as conn:
                conn.execute("BEGIN TRANSACTION")
                try:
                    count = upsert_market_data(frame, symbol, exchange, interval, connection=conn)
                    save_coverage(conn, "fyers", symbol, exchange, interval, left, right, count)
                    conn.execute("COMMIT")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
            records += count
            pending.popleft()
            del frame, future
            logger.info(f"Saved {symbol}:{exchange}:{interval} {left} to {right}: {count} candles")
            submit_next()
        return (
            True,
            {
                "status": "success",
                "symbol": symbol,
                "exchange": exchange,
                "interval": interval,
                "start_date": start_date,
                "end_date": end_date,
                "records": records,
            },
            200,
        )
    except DownloadInterrupted:
        raise
    except Exception as exc:
        logger.exception(f"Historify download failed for {symbol}:{exchange}")
        stop_job = str(getattr(exc, "code", "")) in ("401", "403", "429", "-8", "-16")
        stop_job = stop_job or "daily budget reached" in str(exc)
        return (
            False,
            {"status": "error", "message": str(exc), "records": records, "stop_job": stop_job},
            500,
        )
    finally:
        for _, _, future in pending:
            future.cancel()
        pending.clear()
