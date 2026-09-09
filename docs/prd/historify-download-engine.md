# Historify Download Engine PRD

## Purpose

The download engine runs multi-symbol history jobs outside the request thread, persists per-symbol progress in DuckDB, and emits best-effort Socket.IO updates for the React Historify UI.

## Creation And Execution

`POST /historify/api/jobs` validates the logged-in session, resolves the user's OpenAlgo API key, and submits `create_and_start_job()` with:

- `job_type`: `custom`, `watchlist`, `option_chain`, or `futures_chain`.
- A non-empty array of `{symbol, exchange}` entries.
- `interval`, `start_date`, and `end_date`.
- Optional `config` and `incremental` behavior.

Jobs run on a shared `ThreadPoolExecutor`. `HISTORIFY_MAX_WORKERS` configures its size and defaults to 5.

Fyers jobs additionally use a shared pool of three fetch workers. Set
`HISTORIFY_FETCH_WORKERS` before starting the server to change this (1-4).
This parallelizes date windows within a stock; increasing job workers alone
does not accelerate one job. Candles remain at the requested `1m` or `D`
resolution, including volume and derivative OI where supplied.

DuckDB uses two CPU threads by default (`HISTORIFY_DB_THREADS`, minimum 1)
to leave CPU capacity for the application. Restart the server after changing
this setting. Transactional chunk writes update catalog totals using the
incoming timestamps and their overlap with stored data, rather than recounting
all years for that stock. The statistics endpoint reads catalog totals instead
of scanning the full candle table. Disabled debug logging does not serialize
large broker candle responses.

## Persisted State

The job record stores status, counts, interval, date range, configuration, timestamps, and error details. Each job item stores symbol, exchange, status, record count, timestamps, and its own error.

Current job outcomes include `pending`, `running`, `paused`, `cancelled`, `completed`, `completed_with_errors`, and `failed`. Item outcomes include `pending`, `downloading`, `success`, `error`, and `skipped`.

## Worker Requirements

- Process only pending/downloading items so a checkpointed job can continue.
- Mark an item downloading before its broker request.
- Call the normalized history service and persist returned candles/catalog changes.
- Update job progress counters after every processed item.
- Keep one item's failure from terminating the remaining job.
- Finish as `completed` only with no item failures; otherwise use `completed_with_errors`.
- Clean in-memory running/pause state after completion, cancellation, or fatal error.

## Incremental Behavior

For brokers other than Fyers, incremental jobs inspect stored first and last timestamps:

- Fetch an earlier missing range when requested start precedes stored data.
- Fetch a later missing range when requested end follows stored data.
- Skip an item when stored data already covers the requested range.
- For `1m`, allow the last stored day to be revisited so the intraday tail can be completed.

Fyers jobs use confirmed date-window coverage for all backfills and retries.
They fetch only gaps in that coverage, with 100-calendar-day intraday windows
and 366-day daily windows. Candles, catalog updates, and a window checkpoint
commit in one transaction. An error leaves the window unfinished; successfully
committed earlier windows survive a restart. Direct single-symbol downloads
force a refresh of the requested range.

An explicit `no_data` response or a valid empty candle array completes an
empty window without retries. This handles dates before listing without
guessing an IPO date or stopping at the first empty period. Empty coverage
expires after seven days so provider repairs can be discovered. Today and
yesterday are always refreshed. No candles are synthesized.

Existing candles created by the old downloader have no coverage checkpoints.
The first new job revalidates those ranges because old min/max timestamps
cannot prove that internal chunks succeeded. Upserts preserve candle identity
and do not create duplicate rows. Deleting symbol data clears its checkpoints.
Use `upgrade/migrate_historify_coverage.py` (also registered in
`migrate_all.py`) to create the new table; initialization also creates it.

## Rate Protection

Fyers requests share process-wide pacing across data and account endpoints:
8 requests/second, 190 requests/minute overall, and 150 history requests/minute.
History stops after 90,000 total process-observed account requests in an IST
day, leaving headroom for other activity. These conservative defaults target
the Standard plan. Counters reset with the process; other applications using
the same account are not counted, so broker rate-limit responses remain
authoritative. See [Fyers plan limits](https://support.fyers.in/portal/en/kb/articles/is-fyers-prime-mandatory-to-trade-on-fyers)
and [history request limits](https://github.com/FyersDev/fyers-skills/blob/master/skills/fyers-trading/references/market-data.md).

Fyers jobs omit random stock/batch cooldowns. HTTP 429 retries honour retry
headers; transport failures and server errors have bounded retries.
Invalid symbols and unexplained errors fail immediately with the status,
code, and message. Authentication/quota failures stop the job for retry after
the account recovers. Failed chunks never masquerade as complete history.

Other brokers retain their existing protection:

- Sleep a random `HISTORIFY_DELAY_MIN` to `HISTORIFY_DELAY_MAX` seconds between symbols; defaults are 1 to 3 seconds.
- After every 10 symbols processed in the current run, apply an additional random 5-to-10-second cooldown.
- The history service retains its own API rate behavior; these job delays protect longer broker request windows.

## Control Operations

| Method | Path | Rule |
|---|---|---|
| POST | `/historify/api/jobs/<job_id>/pause` | Only a running job can pause |
| POST | `/historify/api/jobs/<job_id>/resume` | Only a paused job can resume |
| POST | `/historify/api/jobs/<job_id>/cancel` | Running or paused jobs can cancel |
| POST | `/historify/api/jobs/<job_id>/retry` | Failed, pending, and interrupted downloading items are resubmitted |
| DELETE | `/historify/api/jobs/<job_id>` | A running job must be cancelled first |

Pause uses a `threading.Event`; cancellation is checked between items and while paused. Control transitions update DuckDB immediately and emit the corresponding Socket.IO event where implemented.

Fyers also checks controls between chunks and while waiting for responses.
In-flight HTTP requests can finish after cancellation, but are not written;
queued requests are cancelled. HTTP reads have a 30-second timeout.

## Restart Recovery

The executor and pause/cancel signals are process-local. At startup, persisted running or paused jobs with no matching in-memory state are marked failed rather than silently appearing active. The user may then retry failed items.

Click **Resume** for a paused job while the application remains running.
After restarting the application, click **Retry** on the interrupted job;
the button is available even if the failed-symbol count is zero. Finished
items remain finished and committed Fyers windows are reused. A window that
was fetched but not committed can be downloaded again. The recent-day refresh
and expiry of empty coverage described above still apply.

## Ownership And Coverage

- Job service: `services/historify_service.py`
- Routes: `blueprints/historify.py`
- Persistence: `database/historify_db.py`
- Schedule-triggered jobs: `services/historify_scheduler_service.py`
- Acceptance coverage: `docs/bdd/historify_and_tools.feature`
