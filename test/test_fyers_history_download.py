"""Offline regressions for empty history, bounded retries and durable backfill."""

import threading
import time
from datetime import date, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pandas as pd
import pytest

from broker.fyers.api import data, rate_limiter
from database import historify_db
from database.historify_coverage import ensure_coverage_table, get_coverage
from services import historify_download_service as downloads


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setattr(data, "get_br_symbol", lambda symbol, exchange: f"{exchange}:{symbol}-EQ")
    return data.BrokerData("test-token")


def candle(day):
    stamp = int(pd.Timestamp(day, tz="Asia/Kolkata").timestamp()) + 9 * 3600 + 15 * 60
    return [stamp, 100, 102, 99, 101, 1000]


def test_prelisting_no_data_is_not_retried_and_later_data_is_fetched(adapter, monkeypatch):
    calls = []

    def request(endpoint, auth):
        query = parse_qs(urlsplit(endpoint).query)
        calls.append(query)
        if len(calls) == 1:
            return {"s": "no_data", "code": 200, "message": "", "candles": []}
        return {"s": "ok", "candles": [candle(query["range_from"][0])]}

    monkeypatch.setattr(data, "get_api_response", request)
    result = adapter.get_history("IPO", "NSE", "1m", "2021-01-01", "2021-07-19")
    assert len(calls) == 2  # 200 inclusive days, 100 days per call
    assert calls[0]["range_to"] == ["2021-04-10"]
    assert calls[1]["range_from"] == ["2021-04-11"]
    assert len(result) == 1
    assert result.oi.tolist() == [0]


@pytest.mark.parametrize(
    "response",
    [
        {"s": "error", "message": "", "code": -300},
        {"s": "error", "message": "Token expired", "code": -16},
        {"s": "ok"},  # Missing candles is not a successful empty result.
        {"s": "no_data", "candles": [[1, 2]]},
    ],
)
def test_unknown_or_permanent_errors_fail_immediately(adapter, monkeypatch, response):
    calls = []
    monkeypatch.setattr(data, "get_api_response", lambda *a: calls.append(1) or response)
    with pytest.raises(data.FyersHistoryError, match="status="):
        adapter.get_history("IPO", "NSE", "1m", "2021-01-01", "2022-01-01")
    assert len(calls) == 1


def test_transient_error_has_bounded_retries_and_does_not_skip_window(adapter, monkeypatch):
    calls, sleeps = [], []
    monkeypatch.setattr(
        data, "get_api_response", lambda *a: calls.append(1) or {"s": "error", "code": 503}
    )
    monkeypatch.setattr(data.time, "sleep", sleeps.append)
    with pytest.raises(data.FyersHistoryError):
        adapter.get_history("IPO", "NSE", "1m", "2021-01-01", "2022-01-01")
    assert len(calls) == 4
    assert sleeps == [1, 2, 4]


def test_daily_windows_and_oi_are_preserved(adapter, monkeypatch):
    calls = []

    def request(endpoint, auth):
        query = parse_qs(urlsplit(endpoint).query)
        calls.append(query)
        row = candle(query["range_from"][0]) + [123]
        return {"s": "ok", "candles": [row, row]}

    monkeypatch.setattr(data, "get_api_response", request)
    result = adapter.get_history("FUT", "NFO", "D", "2020-01-01", "2021-01-01")
    assert len(calls) == 2
    assert calls[0]["range_to"] == ["2020-12-31"]
    assert all(q["oi_flag"] == ["1"] for q in calls)
    assert result.oi.tolist() == [123, 123]


@pytest.fixture
def isolated_db(monkeypatch, tmp_path):
    monkeypatch.setattr(historify_db, "HISTORIFY_DB_PATH", str(tmp_path / "history.duckdb"))
    historify_db.init_database()
    yield


def fake_frame(day):
    return pd.DataFrame(
        [candle(day)], columns=["timestamp", "open", "high", "low", "close", "volume"]
    )


def test_failure_retains_committed_chunks_and_resume_repairs_gap(isolated_db, monkeypatch):
    calls = []

    def fetch(self, symbol, exchange, interval, start, end):
        calls.append(start)
        if start == "2021-04-11":
            raise data.FyersHistoryError("temporary outage", 503)
        return fake_frame(start)

    monkeypatch.setattr(data.BrokerData, "get_history", fetch)
    result = downloads.download_fyers("IPO", "NSE", "1m", "2021-01-01", "2021-10-27", "token")
    assert result[0] is False
    assert result[1]["records"] == 1
    with historify_db.get_connection() as conn:
        assert conn.execute("SELECT count(*) FROM market_data").fetchone()[0] == 1
        assert len(get_coverage(conn, "fyers", "IPO", "NSE", "1m")) == 1
    calls.clear()

    def repaired(self, symbol, exchange, interval, start, end):
        calls.append(start)
        return fake_frame(start)

    monkeypatch.setattr(data.BrokerData, "get_history", repaired)
    assert downloads.download_fyers("IPO", "NSE", "1m", "2021-01-01", "2021-10-27", "token")[0]
    assert "2021-01-01" not in calls
    assert "2021-04-11" in calls
    with historify_db.get_connection() as conn:
        assert conn.execute("SELECT count(*) FROM market_data").fetchone()[0] == 3
        assert conn.execute("SELECT record_count FROM data_catalog").fetchone()[0] == 3
    calls.clear()
    assert downloads.download_fyers("IPO", "NSE", "1m", "2021-01-01", "2021-10-27", "token")[0]
    assert calls == []


def test_coverage_failure_rolls_back_candles_and_catalog(isolated_db, monkeypatch):
    monkeypatch.setattr(data.BrokerData, "get_history", lambda *a: fake_frame("2021-01-01"))
    monkeypatch.setattr(
        downloads,
        "save_coverage",
        lambda *a: (_ for _ in ()).throw(RuntimeError("checkpoint write failed")),
    )
    assert not downloads.download_fyers("IPO", "NSE", "1m", "2021-01-01", "2021-01-01", "token")[0]
    with historify_db.get_connection() as conn:
        assert conn.execute("SELECT count(*) FROM market_data").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM data_catalog").fetchone()[0] == 0


def test_empty_windows_checkpoint_and_deletion_invalidates_them(isolated_db, monkeypatch):
    calls = []
    monkeypatch.setattr(
        data.BrokerData, "get_history", lambda *a: calls.append(1) or pd.DataFrame()
    )
    args = ("IPO", "NSE", "1m", "2021-01-01", "2021-01-01", "token")
    assert downloads.download_fyers(*args)[0]
    assert downloads.download_fyers(*args)[0]
    assert len(calls) == 1
    assert historify_db.delete_market_data("IPO", "NSE", "1m")[0]
    assert downloads.download_fyers(*args)[0]
    assert len(calls) == 2


def test_parallel_fetch_is_bounded(isolated_db, monkeypatch):
    lock = threading.Lock()
    active = peak = 0

    def fetch(self, symbol, exchange, interval, start, end):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            time.sleep(0.15)
            return fake_frame(start)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(data.BrokerData, "get_history", fetch)
    assert downloads.download_fyers("IPO", "NSE", "1m", "2021-01-01", "2022-01-01", "token")[0]
    assert 1 < peak <= downloads._workers


def test_cancel_before_submit_makes_no_requests(isolated_db, monkeypatch):
    monkeypatch.setattr(
        data.BrokerData, "get_history", lambda *a: pytest.fail("Unexpected request")
    )

    def cancel():
        raise downloads.DownloadInterrupted()

    with pytest.raises(downloads.DownloadInterrupted):
        downloads.download_fyers(
            "IPO", "NSE", "1m", "2021-01-01", "2022-01-01", "token", checkpoint=cancel
        )


def test_missing_windows_merge_overlaps_without_losing_boundaries():
    d = date(2021, 1, 1)
    covered = [
        (d + timedelta(days=2), d + timedelta(days=4)),
        (d + timedelta(days=4), d + timedelta(days=7)),
    ]
    assert list(downloads.missing_windows(d, d + timedelta(days=10), covered, 100)) == [
        (d, d + timedelta(days=1)),
        (d + timedelta(days=8), d + timedelta(days=10)),
    ]


def test_limiter_applies_history_and_total_minute_budgets(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(rate_limiter.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(rate_limiter.time, "time", lambda: clock[0])
    monkeypatch.setattr(
        rate_limiter.time, "sleep", lambda delay: clock.__setitem__(0, clock[0] + delay)
    )
    monkeypatch.setattr(rate_limiter, "_last_call_time", 0)
    monkeypatch.setattr(rate_limiter, "_day_key", None)
    monkeypatch.setattr(rate_limiter, "_minute_calls", __import__("collections").deque())
    monkeypatch.setattr(rate_limiter, "_history_calls", __import__("collections").deque())
    dispatch = []
    for _ in range(400):
        rate_limiter.apply_rate_limit(history=True)
        dispatch.append(clock[0])
    assert all(dispatch[i] - dispatch[i - 150] >= 60 for i in range(150, len(dispatch)))
    assert all(
        b - a >= rate_limiter.MIN_INTERVAL for a, b in zip(dispatch, dispatch[1:], strict=False)
    )
    # History leaves room for interactive requests in the same minute.
    before = clock[0]
    rate_limiter.apply_rate_limit()
    assert clock[0] - before < 1
    all_calls = []
    for _ in range(400):
        rate_limiter.apply_rate_limit()
        all_calls.append(clock[0])
    assert all(all_calls[i] - all_calls[i - 190] >= 60 for i in range(190, len(all_calls)))


def test_history_daily_budget_keeps_capacity_for_account_calls(monkeypatch):
    day = int((time.time() + 19800) // 86400)
    monkeypatch.setattr(rate_limiter, "_day_key", day)
    monkeypatch.setattr(rate_limiter, "_day_calls", rate_limiter.HISTORY_DAILY_BUDGET)
    with pytest.raises(RuntimeError, match="daily budget"):
        rate_limiter.apply_rate_limit(history=True)


def test_migration_is_idempotent_and_preserves_existing_rows(isolated_db):
    historify_db.upsert_market_data(fake_frame("2021-01-01"), "IPO", "NSE", "1m")
    with historify_db.get_connection() as conn:
        conn.execute("DROP TABLE historify_download_coverage")
        ensure_coverage_table(conn)
        ensure_coverage_table(conn)
        assert conn.execute("SELECT count(*) FROM market_data").fetchone()[0] == 1


def test_http_429_is_not_multiplied_by_history_retries(adapter, monkeypatch):
    calls = []
    waits = []
    request = httpx.Request("GET", "https://api-t1.fyers.in/data/history")

    class Client:
        def get(self, *args, **kwargs):
            calls.append(kwargs)
            return httpx.Response(429, request=request, headers={"Retry-After": "2"})

    monkeypatch.setattr(data, "get_httpx_client", Client)
    monkeypatch.setattr(data, "apply_rate_limit", lambda **kw: None)
    monkeypatch.setattr(data.time, "sleep", waits.append)
    with pytest.raises(data.FyersHistoryError) as failure:
        adapter.get_history("IPO", "NSE", "1m", "2021-01-01", "2022-01-01")
    assert failure.value.code == 429
    assert len(calls) == 4
    assert waits == [2, 2, 2]
    assert all(call["timeout"] == 30.0 for call in calls)


def test_expired_empty_coverage_is_rechecked(isolated_db, monkeypatch):
    calls = []
    monkeypatch.setattr(
        data.BrokerData, "get_history", lambda *a: calls.append(1) or pd.DataFrame()
    )
    args = ("IPO", "NSE", "1m", "2021-01-01", "2021-01-01", "token")
    assert downloads.download_fyers(*args)[0]
    with historify_db.get_connection() as conn:
        conn.execute(
            "UPDATE historify_download_coverage SET checked_at = now() - INTERVAL '8 days'"
        )
    assert downloads.download_fyers(*args)[0]
    assert len(calls) == 2


def test_force_refresh_upserts_without_inflating_count(isolated_db, monkeypatch):
    monkeypatch.setattr(data.BrokerData, "get_history", lambda *a: fake_frame("2021-01-01"))
    args = ("IPO", "NSE", "1m", "2021-01-01", "2021-01-01", "token")
    assert downloads.download_fyers(*args)[0]
    assert downloads.download_fyers(*args, resume=False)[0]
    with historify_db.get_connection() as conn:
        assert conn.execute("SELECT count(*) FROM market_data").fetchone()[0] == 1
        assert conn.execute("SELECT record_count FROM data_catalog").fetchone()[0] == 1


def test_auth_failure_stops_backfill_with_retryable_job_state(isolated_db, monkeypatch):
    def expired(*args):
        raise data.FyersHistoryError("Token expired", -16)

    monkeypatch.setattr(data.BrokerData, "get_history", expired)
    success, response, _ = downloads.download_fyers(
        "IPO", "NSE", "1m", "2021-01-01", "2021-01-01", "token"
    )
    assert not success
    assert response["stop_job"]
    with historify_db.get_connection() as conn:
        assert get_coverage(conn, "fyers", "IPO", "NSE", "1m") == []


def test_fyers_job_uses_checkpoints_and_retries_interrupted_items(isolated_db, monkeypatch):
    from services import historify_service as jobs

    monkeypatch.setattr(jobs, "get_auth_token_broker", lambda *a: ("token", "fyers"))
    monkeypatch.setattr(jobs, "_emit_progress", lambda *a: None)
    monkeypatch.setattr(jobs, "_emit_job_complete", lambda *a: None)
    monkeypatch.setattr(data.BrokerData, "get_history", lambda *a: fake_frame("2021-01-01"))
    assert historify_db.create_download_job(
        "test-job",
        "custom",
        [{"symbol": "IPO", "exchange": "NSE"}],
        "1m",
        "2021-01-01",
        "2021-01-01",
    )[0]
    items = historify_db.get_job_items("test-job")
    historify_db.update_job_item_status(items[0]["id"], "downloading")
    historify_db.update_job_status("test-job", "failed")

    class InlineExecutor:
        def submit(self, fn, *args):
            fn(*args)

    monkeypatch.setattr(jobs, "_job_executor", InlineExecutor())
    assert jobs.retry_failed_items("test-job", "test-api-key")[0]
    assert historify_db.get_download_job("test-job")["status"] == "completed"
    assert historify_db.get_job_items("test-job")[0]["status"] == "success"
    assert "test-job" not in jobs._running_jobs
    assert "test-job" not in jobs._paused_jobs


def test_retry_cannot_start_a_second_worker_for_a_paused_job(monkeypatch):
    from services import historify_service as jobs

    monkeypatch.setattr(historify_db, "get_download_job", lambda *a: {"status": "paused"})
    assert jobs.retry_failed_items("paused-job", "test-api-key")[2] == 400


def test_disabled_debug_logging_does_not_serialize_candles(adapter, monkeypatch):
    request = httpx.Request("GET", "https://api-t1.fyers.in/data/history")
    response = httpx.Response(
        200, request=request, json={"s": "ok", "candles": [candle("2021-01-01")]}
    )

    class Client:
        def get(self, *args, **kwargs):
            return response

    monkeypatch.setattr(data, "get_httpx_client", Client)
    monkeypatch.setattr(data, "apply_rate_limit", lambda **kw: None)
    monkeypatch.setattr(
        data.json, "dumps", lambda *a, **kw: pytest.fail("Unexpected JSON formatting")
    )
    assert data.get_api_response("/data/history?symbol=test", "token")["s"] == "ok"


def test_chunk_catalog_counts_handle_overlap_gaps_and_duplicate_timestamps(isolated_db):
    # Existing endpoints deliberately enclose an internal gap.
    initial = pd.concat([fake_frame("2021-01-01"), fake_frame("2021-01-03")])
    historify_db.upsert_market_data(initial, "IPO", "NSE", "1m")
    incoming = pd.concat(
        [
            fake_frame("2020-12-31"),
            fake_frame("2021-01-01"),
            fake_frame("2021-01-02"),
            fake_frame("2021-01-03"),
            fake_frame("2021-01-04"),
            fake_frame("2021-01-04"),
        ]
    )
    with historify_db.get_connection() as conn:
        conn.execute("BEGIN TRANSACTION")
        assert historify_db.upsert_market_data(incoming, "IPO", "NSE", "1m", connection=conn) == 5
        conn.execute("COMMIT")
        actual = conn.execute(
            "SELECT MIN(timestamp), MAX(timestamp), COUNT(*) FROM market_data"
        ).fetchone()
        catalog = conn.execute(
            "SELECT first_timestamp, last_timestamp, record_count FROM data_catalog"
        ).fetchone()
        assert catalog == actual
        assert catalog[2] == 5


def test_stats_count_symbols_once_across_intervals_and_db_limits_cpu(isolated_db):
    frame = fake_frame("2021-01-01")
    historify_db.upsert_market_data(frame, "IPO", "NSE", "1m")
    historify_db.upsert_market_data(frame, "IPO", "NSE", "D")
    historify_db.upsert_market_data(frame, "SECOND", "NSE", "1m")
    stats = historify_db.get_database_stats()
    assert stats["total_records"] == 3
    assert stats["total_symbols"] == 2
    with historify_db.get_connection() as conn:
        assert (
            conn.execute("SELECT current_setting('threads')").fetchone()[0]
            == historify_db.HISTORIFY_DB_THREADS
        )
