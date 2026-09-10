# Fyers stock scanner: volume shockers and daily movers

Implemented on 10 September 2026. This phase adds the scanner backend and a terminal command. The frontend, navigation and visual design are deferred until requested.

## What it scans

The default universe is all NSE EQ-series instruments in the installed Fyers master contract. Indices, derivatives, BSE and non-EQ series are excluded. Some ETFs are classified as EQ by the broker and may be included; this is not a curated index-constituent universe. Supply a symbol list to scan a smaller universe.

The date is calculated at each scan start in `Asia/Kolkata`. Results are snapshots for that calendar day, not a permanently hardcoded date. A quote must have a valid Fyers trade timestamp from that day, a positive last price and previous close, and positive traded volume. Yesterday's quotes never become today's movers. Before trading or on a holiday, no qualifying quotes means empty lists with exclusion reasons. A total quotes API failure is reported as a failed scan.

## Calculations

```text
Average volume = sum(volume of previous N completed daily candles) / N
RVOL = today's cumulative traded volume / average volume
Change % = (last traded price - previous closing price) / previous closing price * 100
```

The default `N` is **5 trading sessions**, representing a weekly average of daily volume. It is configurable from 1 through 30. The history request ends yesterday; any current-day candle returned by the broker is excluded defensively. Daily bars are sorted and identical duplicate dates are collapsed. The most recent N available completed daily bars are used, so weekends and holidays with no bars do not count as zero-volume days. Returned zero-volume daily bars do count. Dates used in each baseline are included in the response. Suspensions or missing broker bars can make this window span more calendar days.

The default volume-shocker condition is **RVOL > 1**, meaning today's volume is above the weekly daily average. Set `min_rvol` to 2 or 5 for a stricter filter; the comparison remains strictly greater than the selected threshold. The comparison uses unrounded values.

This compares today's cumulative volume so far with prior **full-day** volume. It is not adjusted for the elapsed time of day and does not use five-minute candles or a projected end-of-day volume.

- Volume shockers: descending RVOL, then descending percentage change, then symbol.
- Top gainers: positive percentage changes, descending; symbol breaks ties.
- Top losers: negative percentage changes, ascending; symbol breaks ties.
- Unchanged prices are excluded from both movers lists.
- Missing/invalid history, fewer than N completed bars, or a zero average means no RVOL. An otherwise valid price quote can still rank among gainers or losers.
- Price and minimum-volume filters apply to all three lists. The RVOL threshold applies only to the volume-shocker list.

## Daily use before the UI is built

Start OpenAlgo normally and log in to Fyers:

```powershell
uv run app.py
```

In a second terminal at the project root, run:

```powershell
# All NSE EQ instruments; previous five sessions; today's volume above average
uv run scripts/market_scanner.py

# Smaller scan, with a stricter RVOL threshold and 10 results per list
uv run scripts/market_scanner.py --symbols ATHERENERG RELIANCE SBIN --min-rvol 2 --limit 10

# Optional daily result file (runtime output is kept out of Git)
uv run scripts/market_scanner.py --output tmp/market-scanner-today.json
```

The command uses your stored Fyers login without printing its token. If multiple active Fyers logins exist, provide `--username YOUR_OPENALGO_USERNAME`. It prints progress and all three result lists. It does not submit orders. The terminal command runs a separate scanner process, so its results are printed/exported rather than appearing in the web process's results API. Avoid simultaneous full-universe scans in multiple processes because they share the broker account quota.

No new dependencies are required. The frontend requires no rebuild for these backend endpoints. Restart the app to load the registered blueprint. Starting the app makes the API available; it does not automatically launch a market-wide scan before login.

## Backend API

All routes require a valid OpenAlgo web session for the logged-in Fyers user and return `Cache-Control: no-store`. POST requests retain the app's normal CSRF protection. These are session APIs, not `/api/v1` API-key endpoints.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/market-scanner/api/universe` | Eligible NSE symbols and universe size |
| POST | `/market-scanner/api/scan` | Start today's background scan; HTTP 202 |
| GET | `/market-scanner/api/results` | Progress, results and per-symbol exclusions |
| POST | `/market-scanner/api/cancel` | Request cancellation between broker calls |

Example scan body; `{}` scans the full default universe:

```json
{
  "symbols": ["ATHERENERG", "RELIANCE", "SBIN"],
  "lookback_days": 5,
  "min_rvol": 1,
  "min_volume": 0,
  "min_price": 0,
  "max_price": 1000000000,
  "limit": 20
}
```

`limit` is 1–500 per list. Custom symbol lists accept up to 5,000 valid master-contract symbols. Unknown options, unknown symbols, invalid ranges, booleans in numeric fields and non-finite numbers are rejected.

Poll results while a scan runs. Query parameters `min_rvol`, `min_volume`, `min_price`, `max_price` and `limit` filter saved results without new Fyers calls. Changing the symbol universe or lookback requires a new scan. Repeating the same active request reuses its scan ID. A different concurrent request returns HTTP 409; scan starts have a 30-second cooldown per user after the previous start.

The response's `data` object contains:

- `scan_id`, `session_date`, `state`, `phase`, `started_at`, `updated_at`, `completed_at`.
- Progress: `total`, `quotes_processed`, `baseline_total`, `baselines_processed`, `cached_baselines`.
- Coverage: `valid_quotes`, `valid_baselines`, `partial`, `issues` and `error`.
- `volume_shockers`, `top_gainers`, `top_losers`, and untruncated `matching_counts`.
- Each row includes symbol/name, LTP, previous close, absolute/percentage change, today's volume, average volume, RVOL, baseline dates/status, quote fetch time and last-trade time.

States are `running`, `completed`, `cancelled`, or `failed`. During the initial quote pass the scanner prepares price movers; they remain available while historical baselines load. All quotes are fetched again after baseline loading so final rankings do not use the pre-warmup prices. Quote batches have individual timestamps and are not simultaneous exchange-wide snapshots. A running, cancelled, failed, or incomplete-coverage scan has `partial: true`.

After the India date changes, old results have `stale: true` and empty ranked lists until a new scan starts. Results do not automatically refresh intraday; run another scan for a new snapshot. Timestamps distinguish an older same-day snapshot from a current one.

## Caching and resource use

The implementation reuses the existing Fyers HTTP wrapper, connection pool, rate limiter and bounded HTTP-429 retries. If those short retries exhaust a minute quota, the scanner enters `phase: rate_limited`, waits 60 seconds, and retries; one further 120-second cooldown is allowed before that request fails. `rate_limit_retries` reports the cooldown count. Waiting is cancellable and checks for India date rollover. Broker authentication failures stop immediately. Quote requests are batched in groups of at most 50. Only instruments with a qualifying quote today need history, so closed-market scans avoid unnecessary history calls. A first full-universe scan of approximately 2,600 instruments can take around 20 minutes or longer under the broker's shared history budget and other activity on the account. Progress remains available while it runs.

Validated completed daily volume bars are cached in `db/market_scanner.db`, keyed by Fyers symbol and India scan date. Subsequent same-day scans reuse them across application restarts. Cache entries older than seven days are pruned. API errors and malformed history are not cached as successful empty data. The cache stores no credentials and no live quotes.

Results are kept in memory, separately for each user, with one active worker and at most 16 retained user results per process. Restarting loses results but keeps baselines. The supported daily launch is the requested single-process `uv run app.py`; a multi-worker deployment would need shared job coordination/results storage. Cancellation takes effect between broker calls, after any in-flight request/retry returns.

## Files changed

| File | Responsibility |
| --- | --- |
| `app.py` | Registers the scanner blueprint |
| `blueprints/market_scanner.py` | Authenticated, CSRF-protected backend API |
| `services/market_scanner_provider.py` | Fyers credentials, master universe, quotes/history requests |
| `services/market_scanner_service.py` | Validation, RVOL/rankings, background jobs and filtering |
| `database/market_scanner_db.py` | Persistent daily baseline cache |
| `scripts/market_scanner.py` | Daily terminal command |
| `test/test_market_scanner.py`, `test/test_market_scanner_routes.py` | Offline calculation/lifecycle/API regressions |

No frontend source, navigation, theme, broker order execution, or existing market-data adapter behavior is changed.

## Verification on 10 September 2026

- 57 offline tests passed, including calculations, cache persistence, batching, cancellation, India date rollover, authentication/CSRF, broker errors, and bounded rate-limit cooldowns. Ruff lint/format and Python syntax checks also passed.
- A full configured-universe scan completed at **15:22:16 IST**: 2,643 instruments requested, 2,637 valid current-day quotes, and 2,633 usable five-session volume baselines.
- The snapshot matched 859 instruments above 1x RVOL, 1,000 gainers and 1,603 losers. The saved JSON contains the top 20 per list and the full matching counts. The EQ universe includes broker-classified ETFs as described above.
- Coverage was partial: six stale/invalid quotes, three instruments with fewer than five completed daily bars, and one instrument that became quote-eligible after baseline loading and therefore had no baseline in this snapshot. Exclusion reasons are reported in `issues`; no missing values were invented.
- The first full run exhausted a Fyers quota after 2,327 cached baselines. After adding and testing the bounded cooldown handling, the resumed run reused those 2,327 baselines and completed. The resumed run required no additional cooldowns.
- All saved ranked rows were independently checked against the cached five-session volumes and percentage-change formula, including date and sort-order checks.

Local snapshot: `tmp/market-scanner-today.json`. This is an intraday verification snapshot, not a closing-price report. It stays outside Git and can be replaced by another scan.

To rerun the offline checks:

```powershell
uv run --no-sync python -m pytest test/test_market_scanner.py test/test_market_scanner_routes.py -q -p no:cacheprovider
```

## API references

Fyers documents up to 50 symbols per quotes request in its [quotes API guidance](https://support.fyers.in/portal/en/kb/articles/how-do-i-retrieve-quotes-data-for-one-or-more-symbols-using-the-quotes-api). Request parameters, quote fields and the historical candle layout were checked against [Fyers' market-data reference](https://github.com/FyersDev/fyers-skills/blob/master/skills/fyers-trading/references/market-data.md).
