"""Daily NSE RVOL and percentage movers; no orders and no frontend dependency."""

import copy
import math
import time
import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo

from services.market_scanner_provider import (
    FyersScannerProvider,
    ScannerError,
    get_fyers_token,
    load_universe,
)
from utils.logging import get_logger
from utils.real_threading import Event, Lock, Thread

IST = ZoneInfo("Asia/Kolkata")
logger = get_logger(__name__)


def now_ist():
    return datetime.now(IST)


def number(value):
    if isinstance(value, bool):
        raise ValueError("Boolean is not a numeric market value")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Market values must be finite")
    return result


def quote_time(value):
    """Fyers tt is epoch seconds; tolerate numeric strings and milliseconds."""
    stamp = number(value)
    if stamp > 100_000_000_000:
        stamp /= 1000
    return datetime.fromtimestamp(stamp, IST)


def validate_options(data):
    if not isinstance(data, dict):
        raise ScannerError("Send a JSON object containing scanner options.")
    allowed = {
        "symbols",
        "lookback_days",
        "min_rvol",
        "min_volume",
        "min_price",
        "max_price",
        "limit",
    }
    unknown = set(data) - allowed
    if unknown:
        raise ScannerError("Unknown scanner option: " + ", ".join(sorted(unknown)))
    result = {}
    for key, default, minimum, maximum in (
        ("lookback_days", 5, 1, 30),
        ("limit", 20, 1, 500),
        ("min_rvol", 1, 0, 1000),
        ("min_volume", 0, 0, 1e15),
        ("min_price", 0, 0, 1e9),
        ("max_price", 1e9, 0, 1e9),
    ):
        try:
            value = number(data.get(key, default))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ScannerError(f"{key} must be a finite number.") from exc
        if not minimum <= value <= maximum:
            raise ScannerError(f"{key} must be between {minimum:g} and {maximum:g}.")
        if key in {"lookback_days", "limit"}:
            if not value.is_integer():
                raise ScannerError(f"{key} must be an integer.")
            value = int(value)
        result[key] = value
    if result["min_price"] > result["max_price"]:
        raise ScannerError("min_price cannot exceed max_price.")
    symbols = data.get("symbols")
    if symbols is not None:
        if not isinstance(symbols, list) or not 1 <= len(symbols) <= 5000:
            raise ScannerError("symbols must be a nonempty list of up to 5000 NSE symbols.")
        if any(
            not isinstance(symbol, str) or not symbol.strip() or len(symbol) > 50
            for symbol in symbols
        ):
            raise ScannerError("Each symbol must be a nonempty string of at most 50 characters.")
        symbols = sorted({symbol.strip().upper() for symbol in symbols})
    result["symbols"] = symbols
    return result


def normalize_history(candles, session_date):
    """Validate daily volumes, exclude today, collapse equal duplicate candles."""
    by_date = {}
    for candle in candles:
        if not isinstance(candle, (list, tuple)) or len(candle) < 6:
            raise ValueError("Invalid historical candle")
        day = quote_time(candle[0]).date()
        if day >= session_date:
            continue
        volume = number(candle[5])
        if volume < 0:
            raise ValueError("Negative historical volume")
        day = day.isoformat()
        if day in by_date and by_date[day] != volume:
            raise ValueError("Conflicting daily volumes")
        by_date[day] = volume
    return [{"date": day, "volume": by_date[day]} for day in sorted(by_date)[-30:]]


def make_row(instrument, quote, history, session_date, lookback_days, fetched_at):
    """A bad baseline must not remove a valid price mover."""
    trade_time = quote_time(quote.get("tt"))
    if trade_time.date() != session_date or trade_time > fetched_at:
        raise ValueError("stale_or_invalid_quote_date")
    ltp = number(quote.get("lp"))
    previous_close = number(quote.get("prev_close_price"))
    volume = number(quote.get("volume"))
    if ltp <= 0 or previous_close <= 0 or volume <= 0:
        raise ValueError("invalid_price_or_no_trades")
    row = {
        "symbol": instrument["symbol"],
        "exchange": "NSE",
        "name": instrument["name"],
        "ltp": ltp,
        "previous_close": previous_close,
        "change": ltp - previous_close,
        "change_percent": (ltp - previous_close) / previous_close * 100,
        "volume": volume,
        "last_trade_at": trade_time.isoformat(),
        "quote_fetched_at": fetched_at.isoformat(),
        "average_volume": None,
        "rvol": None,
        "baseline_dates": [],
        "baseline_status": "history_unavailable",
    }
    if not math.isfinite(row["change_percent"]):
        raise ValueError("invalid_percentage_change")
    if history is not None:
        selected = history[-lookback_days:]
        row["baseline_dates"] = [item["date"] for item in selected]
        row["baseline_status"] = "insufficient_history"
        if len(selected) == lookback_days:
            average = math.fsum(item["volume"] for item in selected) / lookback_days
            row["average_volume"] = average
            row["baseline_status"] = "zero_average_volume"
            if average > 0:
                row["rvol"] = volume / average
                if not math.isfinite(row["rvol"]):
                    raise ValueError("invalid_relative_volume")
                row["baseline_status"] = "ready"
    return row


def rank_rows(rows, options):
    filtered = [
        row
        for row in rows
        if options["min_price"] <= row["ltp"] <= options["max_price"]
        and row["volume"] >= options["min_volume"]
    ]
    shockers = sorted(
        (row for row in filtered if row["rvol"] is not None and row["rvol"] > options["min_rvol"]),
        key=lambda row: (-row["rvol"], -row["change_percent"], row["symbol"]),
    )
    gainers = sorted(
        (row for row in filtered if row["change_percent"] > 0),
        key=lambda row: (-row["change_percent"], row["symbol"]),
    )
    losers = sorted(
        (row for row in filtered if row["change_percent"] < 0),
        key=lambda row: (row["change_percent"], row["symbol"]),
    )
    limit = options["limit"]
    return {
        "volume_shockers": shockers[:limit],
        "top_gainers": gainers[:limit],
        "top_losers": losers[:limit],
        "matching_counts": {
            "volume_shockers": len(shockers),
            "top_gainers": len(gainers),
            "top_losers": len(losers),
        },
    }


class ScannerManager:
    """One bounded worker per process. Results are scoped to the requesting user."""

    def __init__(self, provider_factory=None, universe_loader=None, cache_factory=None, clock=None):
        self.provider_factory = provider_factory or (
            lambda user: FyersScannerProvider(get_fyers_token(user))
        )
        self.universe_loader = universe_loader or load_universe
        self.cache_factory = cache_factory or self._default_cache
        self.clock = clock or now_ist
        self.lock = Lock()
        self.jobs = {}
        self.active = None
        self._cache = None

    @staticmethod
    def _default_cache():
        from database.market_scanner_db import BaselineCache

        return BaselineCache()

    def start(self, user, data):
        options = validate_options(data)
        # Database lookups run outside the short in-memory lock.
        provider = self.provider_factory(user)
        universe = self.universe_loader()
        if options["symbols"] is not None:
            requested = set(options["symbols"])
            unknown = requested - {item["symbol"] for item in universe}
            if unknown:
                raise ScannerError(
                    "Symbols not found in NSE EQ: " + ", ".join(sorted(unknown)[:10])
                )
            universe = [item for item in universe if item["symbol"] in requested]
        if not universe or len(universe) > 5000:
            raise ScannerError("Select between 1 and 5000 NSE EQ instruments.")
        with self.lock:
            if self.active:
                existing = self.jobs[self.active]
                if existing["user"] == user and existing["options"] == options:
                    return self._snapshot(existing), True
                raise ScannerError(
                    "A stock scan is already running. Wait or cancel your current scan.", 409
                )
            now = self.clock()
            previous = self.jobs.get(user)
            if (
                previous
                and (now - datetime.fromisoformat(previous["started_at"])).total_seconds() < 30
            ):
                raise ScannerError(
                    "Wait 30 seconds between scans; filter the saved results meanwhile.", 429
                )
            # At most one retained job per user and 16 users per process.
            self.jobs = {
                key: job
                for key, job in self.jobs.items()
                if job["session_date"] == now.date().isoformat()
            }
            if user not in self.jobs and len(self.jobs) >= 16:
                oldest = min(self.jobs, key=lambda key: self.jobs[key]["started_at"])
                self.jobs.pop(oldest)
            job = {
                "user": user,
                "scan_id": uuid.uuid4().hex,
                "state": "running",
                "phase": "quotes",
                "session_date": now.date().isoformat(),
                "started_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "completed_at": None,
                "options": options,
                "total": len(universe),
                "baselines_processed": 0,
                "baseline_total": 0,
                "quotes_processed": 0,
                "cached_baselines": 0,
                "rate_limit_retries": 0,
                "rows": [],
                "issues": [],
                "error": None,
                "cancel": Event(),
            }
            self.jobs[user] = job
            self.active = user
            worker = Thread(
                target=self._run, args=(job, provider, universe), daemon=True, name="market-scanner"
            )
            try:
                worker.start()
            except Exception:
                self.active = None
                self.jobs.pop(user, None)
                raise
            return self._snapshot(job), False

    def _snapshot(self, job, overrides=None):
        # Never serialize user identifiers, tokens, thread primitives, or raw rows.
        result = {
            key: copy.deepcopy(value)
            for key, value in job.items()
            if key not in {"user", "cancel", "rows"}
        }
        options = job["options"] if overrides is None else overrides
        result["options"] = copy.deepcopy(options)
        result["stale"] = job["session_date"] != self.clock().date().isoformat()
        result["partial"] = job["state"] != "completed" or bool(job["issues"])
        result["valid_quotes"] = len(job["rows"])
        result["valid_baselines"] = sum(row["rvol"] is not None for row in job["rows"])
        result.update(rank_rows([] if result["stale"] else job["rows"], options))
        return result

    def results(self, user, filters=None):
        with self.lock:
            job = self.jobs.get(user)
            if job is None:
                raise ScannerError("No scan yet. Start a scan for today's NSE stocks.", 404)
            options = None
            if filters:
                if set(filters) - {"min_rvol", "min_volume", "min_price", "max_price", "limit"}:
                    raise ScannerError(
                        "Only price, volume, RVOL and limit filters can change without a new scan."
                    )
                options = validate_options({**job["options"], **filters})
            return self._snapshot(job, options)

    def cancel(self, user):
        with self.lock:
            job = self.jobs.get(user)
            if job is None:
                raise ScannerError("No scan found.", 404)
            if job["state"] == "running":
                job["cancel"].set()
                job["phase"] = "cancelling"
            return self._snapshot(job)

    def _check_stop(self, job):
        if job["cancel"].is_set():
            raise ScannerError("Scan cancelled.", 499)
        if self.clock().date().isoformat() != job["session_date"]:
            raise ScannerError("The India trading date changed. Start a new scan.", 409)

    def _update(self, job, **values):
        with self.lock:
            job.update(values)
            job["updated_at"] = self.clock().isoformat()

    def _issue(self, job, instrument, reason):
        with self.lock:
            job["issues"].append({"symbol": instrument["symbol"], "reason": reason})

    def _wait_for_retry(self, job, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self._check_stop(job)
            job["cancel"].wait(min(1, max(0, deadline - time.monotonic())))

    def _call_broker(self, job, function, *args):
        # The shared HTTP wrapper already retries individual 429s. If its
        # short retries exhaust a minute quota, wait for that window to clear
        # rather than discard a nearly finished full-market scan.
        for attempt in range(3):
            self._check_stop(job)
            try:
                return function(*args)
            except ScannerError as exc:
                if exc.status_code != 429 or attempt == 2:
                    raise
                phase = job["phase"]
                self._update(
                    job, phase="rate_limited", rate_limit_retries=job["rate_limit_retries"] + 1
                )
                self._wait_for_retry(job, 60 * (attempt + 1))
                self._check_stop(job)
                self._update(job, phase=phase)

    def _run(self, job, provider, universe):
        try:
            session_date = date.fromisoformat(job["session_date"])
            histories = {}
            # Publish price movers immediately and avoid warming thousands of
            # baselines on holidays / before the first trade of the day.
            rows, issues = self._fetch_quotes(job, provider, universe, histories)
            self._update(job, rows=rows)
            active_symbols = {row["symbol"] for row in rows}
            eligible = [item for item in universe if item["symbol"] in active_symbols]
            self._update(job, phase="baselines", baseline_total=len(eligible))
            if eligible:
                if self._cache is None:
                    self._cache = self.cache_factory()
                self._cache.prune(job["session_date"])
            failures = 0
            for index, instrument in enumerate(eligible):
                self._check_stop(job)
                symbol = instrument["broker_symbol"]
                try:
                    history = self._cache.get(symbol, job["session_date"])
                    if history is None:
                        history = normalize_history(
                            self._call_broker(job, provider.history, instrument, session_date),
                            session_date,
                        )
                        self._cache.put(symbol, job["session_date"], history)
                    else:
                        self._update(job, cached_baselines=job["cached_baselines"] + 1)
                    histories[symbol] = history
                    failures = 0
                except ScannerError as exc:
                    if exc.status_code in {401, 403, 429}:
                        raise
                    failures += 1
                    self._issue(job, instrument, "history_unavailable")
                    if failures >= 3:
                        # Preserve the independent movers results during a
                        # history outage, and stop hammering the history API.
                        break
                except (ValueError, TypeError, OverflowError, OSError):
                    self._issue(job, instrument, "invalid_history")
                self._update(job, baselines_processed=index + 1)
            if eligible:
                # Refresh after slow cold history loading. Keep the first
                # snapshot visible, with its own timestamps, until replaced.
                self._update(job, phase="refreshing_quotes", quotes_processed=0)
                rows, issues = self._fetch_quotes(job, provider, universe, histories)
            for row in rows:
                if row["baseline_status"] != "ready":
                    issues.append({"symbol": row["symbol"], "reason": row["baseline_status"]})
            # Final issues describe exactly this final snapshot, avoiding stale
            # errors from the preliminary quote pass.
            self._update(job, rows=rows, issues=issues)
            self._check_stop(job)
            self._update(
                job, state="completed", phase="done", completed_at=self.clock().isoformat()
            )
        except ScannerError as exc:
            state = "cancelled" if exc.status_code == 499 else "failed"
            self._update(
                job,
                state=state,
                phase="done",
                error=str(exc),
                completed_at=self.clock().isoformat(),
            )
        except Exception:
            logger.exception("Stock scanner failed")
            self._update(
                job,
                state="failed",
                phase="done",
                error="The scan failed. Check the server logs.",
                completed_at=self.clock().isoformat(),
            )
        finally:
            with self.lock:
                self.active = None

    def _fetch_quotes(self, job, provider, universe, histories):
        rows, issues = [], []
        failures = 0
        session_date = date.fromisoformat(job["session_date"])
        for offset in range(0, len(universe), 50):
            self._check_stop(job)
            batch = universe[offset : offset + 50]
            try:
                quotes = self._call_broker(job, provider.quotes, batch)
                failures = 0
            except ScannerError as exc:
                if exc.status_code in {401, 403, 429}:
                    raise
                failures += 1
                issues.extend(
                    {"symbol": item["symbol"], "reason": "quote_unavailable"} for item in batch
                )
                if failures >= 3:
                    raise ScannerError("Fyers quotes failed repeatedly. Retry later.", 502) from exc
                self._update(job, quotes_processed=offset + len(batch))
                continue
            fetched_at = self.clock()
            self._check_stop(job)
            for instrument in batch:
                quote = quotes.get(instrument["broker_symbol"])
                if quote is None:
                    issues.append({"symbol": instrument["symbol"], "reason": "quote_unavailable"})
                    continue
                try:
                    row = make_row(
                        instrument,
                        quote,
                        histories.get(instrument["broker_symbol"]),
                        session_date,
                        job["options"]["lookback_days"],
                        fetched_at,
                    )
                except (ValueError, TypeError, OverflowError, OSError):
                    issues.append(
                        {"symbol": instrument["symbol"], "reason": "stale_or_invalid_quote"}
                    )
                    continue
                rows.append(row)
            self._update(job, quotes_processed=offset + len(batch))
        if not rows and issues and all(item["reason"] == "quote_unavailable" for item in issues):
            raise ScannerError("Fyers quotes are unavailable. Retry the scan later.", 502)
        return rows, issues


scanner_manager = ScannerManager()
