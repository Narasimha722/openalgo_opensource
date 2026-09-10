"""Offline scanner regressions: session dates, RVOL, ranking and scan lifecycle."""

import time
from datetime import datetime, timedelta
from threading import Event

import pytest

from database.market_scanner_db import BaselineCache
from services.market_scanner_provider import FyersScannerProvider, ScannerError
from services.market_scanner_service import (
    IST,
    ScannerManager,
    make_row,
    normalize_history,
    rank_rows,
    validate_options,
)

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=IST)


def instrument(symbol="ABC"):
    return {
        "symbol": symbol,
        "broker_symbol": f"NSE:{symbol}-EQ",
        "exchange": "NSE",
        "name": symbol,
    }


def quote(price=110, previous=100, volume=2000, at=NOW):
    return {"lp": price, "prev_close_price": previous, "volume": volume, "tt": int(at.timestamp())}


def candles():
    # Five completed sessions, a weekend gap, and today's partial candle.
    return [
        [int(datetime(2026, 9, day, tzinfo=IST).timestamp()), 100, 110, 90, 100, volume]
        for day, volume in [(3, 100), (4, 200), (7, 300), (8, 400), (9, 500), (10, 99999)]
    ]


def row(symbol="ABC", price=110, previous=100, volume=2000, history=None):
    return make_row(instrument(symbol), quote(price, previous, volume), history, NOW.date(), 5, NOW)


def test_rvol_uses_five_completed_sessions_excluding_today_and_duplicates():
    source = candles()
    history = normalize_history(list(reversed(source)) + [source[0]], NOW.date())
    result = row(history=history, volume=600)
    assert result["average_volume"] == 300
    assert result["rvol"] == 2
    assert result["baseline_dates"] == [
        "2026-09-03",
        "2026-09-04",
        "2026-09-07",
        "2026-09-08",
        "2026-09-09",
    ]


def test_conflicting_duplicates_and_negative_history_do_not_bias_average():
    source = candles()
    with pytest.raises(ValueError, match="Conflicting"):
        normalize_history(source + [[*source[0][:5], 999]], NOW.date())
    with pytest.raises(ValueError, match="Negative"):
        normalize_history([[*source[0][:5], -1]], NOW.date())


@pytest.mark.parametrize("volume", [float("nan"), float("inf"), -1, None, True])
def test_invalid_history_volume(volume):
    with pytest.raises((ValueError, TypeError)):
        normalize_history([[*candles()[0][:5], volume]], NOW.date())


def test_insufficient_and_zero_baselines_are_not_divided_or_imputed():
    history = normalize_history(candles(), NOW.date())
    assert row(history=history[:4])["baseline_status"] == "insufficient_history"
    assert row(history=history[:4])["rvol"] is None
    zeroes = [{**item, "volume": 0} for item in history]
    assert row(history=zeroes)["baseline_status"] == "zero_average_volume"
    assert row(history=zeroes)["rvol"] is None
    # Zero-volume completed sessions are valid members of the average.
    history[0]["volume"] = 0
    assert row(history=history)["average_volume"] == 280


def test_gainers_and_losers_rank_percentages_not_absolute_changes():
    rows = [
        row("SMALL", 110, 100),
        row("BIG", 1050, 1000),
        row("LOSS", 90, 100),
        row("LESSLOSS", 980, 1000),
        row("FLAT", 100, 100),
    ]
    ranked = rank_rows(rows, validate_options({"min_rvol": 5}))
    assert [item["symbol"] for item in ranked["top_gainers"]] == ["SMALL", "BIG"]
    assert [item["symbol"] for item in ranked["top_losers"]] == ["LOSS", "LESSLOSS"]
    assert not ranked["volume_shockers"]


def test_threshold_is_strict_and_common_filters_apply_before_limit():
    history = normalize_history(candles(), NOW.date())
    rows = [
        row("EXACT", volume=300, history=history),
        row("ABOVE", volume=600, history=history),
        row("CHEAP", price=2, previous=1, volume=900, history=history),
    ]
    ranked = rank_rows(rows, validate_options({"min_price": 10, "limit": 1}))
    assert [item["symbol"] for item in ranked["volume_shockers"]] == ["ABOVE"]
    assert ranked["matching_counts"]["top_gainers"] == 2
    assert ranked["top_gainers"][0]["symbol"] == "ABOVE"  # deterministic tie break


@pytest.mark.parametrize(
    "change",
    [
        {"tt": int((NOW - timedelta(days=1)).timestamp())},
        {"tt": None},
        {"tt": int((NOW + timedelta(minutes=1)).timestamp())},
        {"prev_close_price": 0},
        {"lp": -1},
        {"volume": 0},
        {"lp": float("nan")},
    ],
)
def test_stale_or_invalid_quotes_are_excluded(change):
    with pytest.raises((ValueError, TypeError)):
        make_row(instrument(), {**quote(), **change}, None, NOW.date(), 5, NOW)


def test_epoch_milliseconds_and_utc_boundary_use_india_date():
    midnight = datetime(2026, 9, 10, 0, 5, tzinfo=IST)
    result = make_row(
        instrument(),
        {**quote(at=midnight), "tt": str(int(midnight.timestamp() * 1000))},
        None,
        midnight.date(),
        5,
        midnight,
    )
    assert result["last_trade_at"].startswith("2026-09-10")


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        {"lookback_days": 0},
        {"lookback_days": 2.5},
        {"lookback_days": 31},
        {"limit": True},
        {"min_rvol": "nan"},
        {"min_volume": -1},
        {"symbols": []},
        {"symbols": "ABC"},
        {"symbols": [4]},
        {"min_price": 10, "max_price": 1},
        {"unknown": 1},
    ],
)
def test_invalid_scan_requests_are_rejected(data):
    with pytest.raises(ScannerError):
        validate_options(data)


def test_options_normalize_symbols_and_accept_query_string_numbers():
    options = validate_options({"symbols": [" abc ", "ABC"], "limit": "10"})
    assert options["symbols"] == ["ABC"]
    assert options["limit"] == 10


class MemoryCache:
    def __init__(self):
        self.entries = {}

    def get(self, symbol, day):
        return self.entries.get((symbol, day))

    def put(self, symbol, day, value):
        self.entries[symbol, day] = value

    def prune(self, day):
        pass


class FakeProvider:
    def __init__(self):
        self.history_calls = 0
        self.quote_batches = []
        self.history_error = None
        self.quote_error = None
        self.quote_date = NOW
        self.block = None

    def quotes(self, items):
        self.quote_batches.append(len(items))
        if self.quote_error:
            raise self.quote_error
        return {item["broker_symbol"]: quote(at=self.quote_date) for item in items}

    def history(self, item, day):
        self.history_calls += 1
        if self.block:
            assert self.block.wait(5)
        if self.history_error:
            raise self.history_error
        return candles()


def manager(provider, count=1, clock=None, cache=None):
    return ScannerManager(
        provider_factory=lambda user: provider,
        universe_loader=lambda: [instrument(f"S{i}") for i in range(count)],
        cache_factory=lambda: cache or MemoryCache(),
        clock=clock or (lambda: NOW),
    )


def finished(scanner, user="alice"):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = scanner.results(user)
        if result["state"] != "running":
            # Completion updates and active-worker release are separate lock operations.
            while scanner.active is not None and time.monotonic() < deadline:
                time.sleep(0.001)
            return result
        time.sleep(0.005)
    pytest.fail("Scan did not finish")


def test_background_scan_batches_refreshes_quotes_and_reuses_daily_baselines():
    provider = FakeProvider()
    now = [NOW]
    scanner = manager(provider, count=51, clock=lambda: now[0])
    scanner.start("alice", {})
    result = finished(scanner)
    assert result["state"] == "completed"
    assert result["valid_quotes"] == result["valid_baselines"] == 51
    assert provider.quote_batches == [50, 1, 50, 1]
    assert provider.history_calls == 51
    now[0] += timedelta(seconds=31)
    scanner.start("alice", {})
    assert finished(scanner)["cached_baselines"] == 51
    assert provider.history_calls == 51
    # Cached results can be filtered without any additional broker calls.
    assert not scanner.results("alice", {"min_rvol": "10"})["volume_shockers"]
    assert len(provider.quote_batches) == 8
    with pytest.raises(ScannerError):
        scanner.results("alice", {"lookback_days": 20})


def test_no_today_quotes_do_not_warm_history_or_show_yesterday_movers():
    provider = FakeProvider()
    provider.quote_date = NOW - timedelta(days=1)
    scanner = manager(provider)
    scanner.start("alice", {})
    result = finished(scanner)
    assert result["state"] == "completed" and result["partial"]
    assert result["valid_quotes"] == provider.history_calls == 0
    assert not result["top_gainers"]


def test_total_quote_outage_is_a_failed_scan_not_an_empty_success():
    provider = FakeProvider()
    provider.quote_error = ScannerError("unavailable", 502)
    scanner = manager(provider)
    scanner.start("alice", {})
    result = finished(scanner)
    assert result["state"] == "failed"
    assert "unavailable" in result["error"]
    assert provider.history_calls == 0


def test_history_outage_keeps_price_movers_and_stops_repeated_history_calls():
    provider = FakeProvider()
    provider.history_error = ScannerError("unavailable", 502)
    scanner = manager(provider, count=6)
    scanner.start("alice", {})
    result = finished(scanner)
    assert result["state"] == "completed" and result["partial"]
    assert provider.history_calls == 3
    assert len(result["top_gainers"]) == 6
    assert not result["volume_shockers"]


@pytest.mark.parametrize("status", [401, 403])
def test_auth_and_rate_limit_failures_stop_scan(status):
    provider = FakeProvider()
    provider.history_error = ScannerError("broker session unavailable", status)
    scanner = manager(provider, count=6)
    scanner.start("alice", {})
    result = finished(scanner)
    assert result["state"] == "failed"
    assert provider.history_calls == 1


def test_concurrent_requests_are_deduplicated_scoped_and_cancellable():
    provider = FakeProvider()
    provider.block = Event()
    scanner = manager(provider)
    result, reused = scanner.start("alice", {})
    try:
        assert not reused
        again, reused = scanner.start("alice", {})
        assert reused and again["scan_id"] == result["scan_id"]
        with pytest.raises(ScannerError) as conflict:
            scanner.start("bob", {})
        assert conflict.value.status_code == 409
        with pytest.raises(ScannerError) as missing:
            scanner.results("bob")
        assert missing.value.status_code == 404
        scanner.cancel("alice")
    finally:
        provider.block.set()
    assert finished(scanner)["state"] == "cancelled"


def test_date_rollover_hides_old_results_and_changes_cache_key():
    provider = FakeProvider()
    now = [NOW]
    scanner = manager(provider, clock=lambda: now[0])
    scanner.start("alice", {})
    finished(scanner)
    now[0] += timedelta(days=1)
    assert scanner.results("alice")["stale"]
    assert not scanner.results("alice")["top_gainers"]
    provider.quote_date = now[0]
    scanner.start("alice", {})
    assert finished(scanner)["cached_baselines"] == 0
    assert provider.history_calls == 2


def test_cache_survives_reopening_and_prunes_old_days(tmp_path):
    url = "sqlite:///" + (tmp_path / "scanner.db").as_posix()
    cache = BaselineCache(url)
    cache.put("NSE:ABC-EQ", "2026-09-01", [])
    baseline = normalize_history(candles(), NOW.date())
    cache.put("NSE:ABC-EQ", "2026-09-10", baseline)
    cache.engine.dispose()
    reopened = BaselineCache(url)
    assert reopened.get("NSE:ABC-EQ", "2026-09-10") == baseline
    assert reopened.get("NSE:ABC-EQ", "2026-09-11") is None
    reopened.prune("2026-09-10")
    assert reopened.get("NSE:ABC-EQ", "2026-09-01") is None
    reopened.engine.dispose()


def test_provider_preserves_quote_time_and_excludes_today_from_history(monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    provider = FyersScannerProvider("test-token")
    endpoints = []

    def request(endpoint):
        endpoints.append(endpoint)
        return {
            "s": "ok",
            "candles": candles(),
            "d": [{"s": "ok", "n": "NSE:ABC-EQ", "v": quote()}],
        }

    monkeypatch.setattr(provider, "_request", request)
    assert provider.quotes([instrument()])["NSE:ABC-EQ"]["tt"] == int(NOW.timestamp())
    provider.history(instrument(), NOW.date())
    query = parse_qs(urlsplit(endpoints[-1]).query)
    assert query["range_to"] == ["2026-09-09"]
    assert query["resolution"] == ["1D"]
    with pytest.raises(ValueError):
        provider.quotes([instrument()] * 51)


@pytest.mark.parametrize(
    ("response", "status"),
    [
        ({"s": "error", "code": -16, "message": "private broker detail"}, 401),
        ({"s": "error", "code": 429}, 429),
        ({"s": "error", "code": 503}, 502),
        ([], 502),
    ],
)
def test_provider_classifies_raw_errors_without_exposing_broker_message(
    monkeypatch, response, status
):
    from broker.fyers.api import data

    monkeypatch.setattr(data, "get_api_response", lambda endpoint, token: response)
    with pytest.raises(ScannerError) as error:
        FyersScannerProvider("test-token")._request("/data/quotes?symbols=NSE:ABC-EQ")
    assert error.value.status_code == status
    assert "private broker detail" not in str(error.value)


def test_provider_rejects_malformed_history_but_accepts_explicit_no_data(monkeypatch):
    provider = FyersScannerProvider("test-token")
    monkeypatch.setattr(provider, "_request", lambda endpoint: {"s": "ok"})
    with pytest.raises(ScannerError):
        provider.history(instrument(), NOW.date())
    monkeypatch.setattr(provider, "_request", lambda endpoint: {"s": "no_data", "candles": []})
    assert provider.history(instrument(), NOW.date()) == []


def test_date_change_during_history_loading_aborts_and_hides_old_rankings():
    provider = FakeProvider()
    provider.block = Event()
    now = [NOW]
    scanner = manager(provider, clock=lambda: now[0])
    scanner.start("alice", {})
    try:
        deadline = time.monotonic() + 3
        while provider.history_calls == 0 and time.monotonic() < deadline:
            time.sleep(0.005)
        assert provider.history_calls == 1
        now[0] += timedelta(days=1)
    finally:
        provider.block.set()
    result = finished(scanner)
    assert result["state"] == "failed" and result["stale"]
    assert not result["top_gainers"]
    assert len(provider.quote_batches) == 1  # no fresh-day quotes mixed into old scan


def test_new_requests_respect_cooldown_and_unknown_symbols():
    provider = FakeProvider()
    scanner = manager(provider)
    with pytest.raises(ScannerError, match="not found"):
        scanner.start("alice", {"symbols": ["UNKNOWN"]})
    assert not provider.quote_batches
    scanner.start("alice", {})
    finished(scanner)
    with pytest.raises(ScannerError) as error:
        scanner.start("alice", {})
    assert error.value.status_code == 429


def test_exhausted_rate_limit_uses_bounded_cooldowns_then_fails(monkeypatch):
    provider = FakeProvider()
    provider.history_error = ScannerError("rate limit", 429)
    scanner = manager(provider)
    waits = []
    monkeypatch.setattr(scanner, "_wait_for_retry", lambda job, seconds: waits.append(seconds))
    scanner.start("alice", {})
    result = finished(scanner)
    assert result["state"] == "failed"
    assert result["rate_limit_retries"] == 2
    assert waits == [60, 120]
    assert provider.history_calls == 3


def test_temporary_throttle_recovers_without_restarting_scan(monkeypatch):
    provider = FakeProvider()
    provider.history_error = ScannerError("rate limit", 429)
    scanner = manager(provider)
    waits = []

    def recover(job, seconds):
        waits.append(seconds)
        assert scanner.results("alice")["phase"] == "rate_limited"
        provider.history_error = None

    monkeypatch.setattr(scanner, "_wait_for_retry", recover)
    scanner.start("alice", {})
    result = finished(scanner)
    assert result["state"] == "completed"
    assert result["valid_baselines"] == 1
    assert waits == [60]


def test_cooldown_can_be_cancelled_before_another_broker_call(monkeypatch):
    provider = FakeProvider()
    provider.history_error = ScannerError("rate limit", 429)
    scanner = manager(provider)
    monkeypatch.setattr(scanner, "_wait_for_retry", lambda job, seconds: scanner.cancel("alice"))
    scanner.start("alice", {})
    assert finished(scanner)["state"] == "cancelled"
    assert provider.history_calls == 1
