# Work Completed: System Configuration and Heikin Ashi CSV Conversion

Last updated: 10 September 2026

## Scope

This document records the work completed in this conversation: noting the supplied computer specifications, converting ATHERENERG five-minute OHLCV data to Heikin Ashi candles, creating a separate code-only repository, and implementing the Fyers stock scanner backend. It is not a history of earlier OpenAlgo development. Sections 1-6 describe the original CSV conversion; later sections record subsequent work.

## 1. System configuration

The following specifications were supplied for use when discussing tools, models, and workloads:

| Component | Specification |
| --- | --- |
| Device name | Sujanix_AI_ML |
| Processor | 11th Gen Intel Core i5-11300H @ 3.10 GHz |
| Installed RAM | 16.0 GB; 15.8 GB usable |
| Graphics | Intel Iris Xe integrated graphics; 128 MB reported |
| Storage | 477 GB total; 151 GB used; approximately 326 GB free when reported |
| System type | 64-bit operating system, x64-based processor |
| Pen and touch | No pen or touch support |

These details were acknowledged as conversation context. No system settings were changed and no separate persistent memory configuration was created. Device ID and Product ID are omitted because they are unnecessary for this work.

## 2. CSV conversion request

Convert the regular ATHERENERG NSE five-minute candles into Heikin Ashi candles and save a separate CSV in the same folder.

**Original file:**

```text
D:\Personal\historify_ATHERENERG_20260908_112029\ATHERENERG_NSE_5m.csv
```

**Created file:**

```text
D:\Personal\historify_ATHERENERG_20260908_112029\ather_hekin_ashi_OHLCV_data.csv
```

The output filename retains the requested spelling, `hekin_ashi`. The candle calculation is Heikin Ashi.

## 3. Input data and output format

| Property | Verified value |
| --- | --- |
| Instrument | ATHERENERG |
| Exchange | NSE |
| Source candle interval | 5 minutes, as identified by the source filename |
| Candle rows | 25,153 |
| First timestamp | 2025-05-06 09:35:00 |
| Last timestamp | 2026-09-09 10:45:00 |
| Output encoding | UTF-8 |

The timestamps above come from the file contents. The folder name contains `20260908`, but the actual data extends to 9 September 2026. Dates and times were retained as supplied, with no timezone conversion.

Both CSVs have the same column names and order:

```csv
date,time,open,high,low,close,volume,oi
```

| Column | Treatment in the new CSV |
| --- | --- |
| `date`, `time` | Preserved from the original row |
| `open` | Replaced with Heikin Ashi open |
| `high` | Replaced with Heikin Ashi high |
| `low` | Replaced with Heikin Ashi low |
| `close` | Replaced with Heikin Ashi close |
| `volume` | Preserved from the original row |
| `oi` | Preserved from the original row |

All rows were retained in their original chronological order. No candles were resampled, added, removed, or filtered. Rows with zero volume were retained.

## 4. Heikin Ashi calculation

For each original candle, let `O`, `H`, `L`, and `C` be its open, high, low, and close.

```text
HA_Close = (O + H + L + C) / 4

First candle:
HA_Open = (O + C) / 2

Every subsequent candle:
HA_Open = (Previous_HA_Open + Previous_HA_Close) / 2

HA_High = max(H, HA_Open, HA_Close)
HA_Low  = min(L, HA_Open, HA_Close)
```

The calculation runs continuously through the file. The previous Heikin Ashi candle carries across session boundaries; the calculation does not restart each trading day.

Values were calculated using Python floating-point arithmetic and written without an explicit decimal-place rounding step. Some output prices therefore contain more decimal places than the source prices. The output OHLC values are derived candle values, while volume and OI remain the original observations.

The first source candle has an open, high, low, and close of `328.0`, so the first converted row is:

```csv
date,time,open,high,low,close,volume,oi
2025-05-06,09:35:00,328.0,328.0,328.0,328.0,1118432.0,0
```

## 5. Implementation and validation

The conversion used a one-off Python script with standard-library CSV handling. No additional packages were installed and no reusable conversion script was saved to the repository.

The following checks passed during conversion:

- The input was nonempty and contained the required columns.
- Input timestamps were in nondecreasing chronological order.
- Original OHLC values were finite and satisfied `low <= open/close <= high`.
- The saved output contained the same 25,153 candle rows.
- All non-OHLC fields matched their original row values.
- Converted candles satisfied `HA_Low <= HA_Open/HA_Close <= HA_High`.
- The final copied file's checksum matched the validated staging file.

On 10 September 2026, both files were read again while preparing this documentation. Every output OHLC value was recalculated and matched the saved value exactly, and all preserved fields and the row count were checked again successfully.

The final CSV is 1,947,533 bytes.

## 6. Files and changes made

| File or area | Result |
| --- | --- |
| `ATHERENERG_NSE_5m.csv` | Read as the conversion source; left unchanged |
| `ather_hekin_ashi_OHLCV_data.csv` | Created in the requested source folder with converted OHLC values |
| `D:\Personal\openalgo\documention.md` | Populated with this work record |
| OpenAlgo application code and configuration | No changes made as part of this work |

The requested output folder was outside the workspace's permitted write area, so saving there required an elevated copy operation. The first copy from temporary storage failed with an access-denied error. The CSV was then staged in the workspace, successfully copied to the requested folder, and checked by checksum. The intermediate workspace CSV was removed after the successful copy.

No application import, strategy backtest, or deployment was performed. The completed deliverable is the validated Heikin Ashi CSV at the path recorded above.

## 7. One-time code-only repository copy

On 10 September 2026, a new root commit was pushed to `main` in [Narasimha722/openalgo_Crypto](https://github.com/Narasimha722/openalgo_Crypto). The repository was cloned to `D:\Personal\openalgo_Crypto` for a separate broker setup.

- Commit: `bb27ca17f4bdc8e3187d89801463123aee6c3d96`.
- Excluded local databases, CSV files, credentials, personal notes, local environments, and frontend build output.
- Cleared saved notebook outputs and replaced 10 embedded example API keys with placeholders in the copied files.
- Verified that the GitHub branch and clean local clone matched.
- Kept the original project and its remotes unchanged. No ongoing synchronization was configured.
- The separate clone requires dependency installation and fresh broker configuration.

## 8. Fyers stock scanner backend

Implemented in `D:\Personal\openalgo` for the existing Fyers setup. The UI, navigation, and visual redesign are deferred at the user's request.

The scanner produces three lists for the current India calendar date:

1. **Volume shockers:** today's cumulative traded volume divided by the average volume of the previous five completed daily candles by default. Stocks qualify when RVOL is strictly above 1; the threshold and 1-30-session lookback are configurable.
2. **Top gainers:** positive percentage change from the previous close, highest first.
3. **Top losers:** negative percentage change from the previous close, lowest first.

It scans NSE EQ-series instruments from the Fyers master contract or a supplied symbol list. Price and volume filters and list limits are supported. Stale quotes are excluded. Missing history only prevents RVOL calculation; valid price movers remain eligible.

Added the authenticated `/market-scanner/api` blueprint and registered it in `app.py`, so the backend loads with `uv run app.py`. Scans run in the background, expose progress and cancellation, and reuse daily volume baselines stored in `db/market_scanner.db`. Fyers quote batches are capped at 50 symbols and use the existing broker rate limiter. Results are fetched again after baseline loading and retain per-quote timestamps.

Until the UI is built, the daily terminal command is:

```powershell
uv run scripts/market_scanner.py
```

Log in to Fyers through OpenAlgo first. A smaller example is:

```powershell
uv run scripts/market_scanner.py --symbols ATHERENERG RELIANCE SBIN --min-rvol 2 --limit 10
```

An actual three-symbol scan on 10 September 2026 returned all three current-day quotes and valid five-session baselines. ATHERENERG showed approximately 2.06x RVOL and +4.68% price change in that snapshot. This was a sample verification, not the market-wide ranking. Offline tests also cover the calculations, stale dates, missing data, cache reuse, scan lifecycle, session authentication, and CSRF protection.

Full calculation definitions, API payloads, operating limits, and file responsibilities are documented in [docs/api/market-scanner.md](docs/api/market-scanner.md).

The full NSE EQ scan completed at **15:22:16 IST on 10 September 2026**, covering 2,643 requested instruments, 2,637 valid quotes, and 2,633 usable five-session baselines. It found 859 instruments above 1x RVOL, 1,000 gainers, and 1,603 losers. The universe includes ETFs classified as EQ by Fyers. The [saved local snapshot](tmp/market-scanner-today.json) contains the top 20 results per list, matching counts, and exclusion reasons. Six quotes were stale/invalid, three instruments had insufficient history, and one newly quote-eligible instrument lacked a baseline in this snapshot.

A full-scan Fyers rate-limit failure led to adding tested, cancellable cooldowns of 60 and then 120 seconds when the existing short HTTP retries are exhausted. The successful resumed run reused 2,327 cached baselines. Final verification: **57 offline tests passed**, lint/format and syntax checks passed, and the saved ranked rows were independently recalculated against their cached volumes. This is a local backend implementation; no UI work was started.
