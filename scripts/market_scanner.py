"""Run today's Fyers stock scan from the terminal while the UI is deferred."""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", help="OpenAlgo username (auto-selects a single Fyers login)")
    parser.add_argument(
        "--symbols", nargs="+", help="NSE symbols; defaults to all NSE EQ instruments"
    )
    parser.add_argument("--lookback-days", type=int, default=5)
    parser.add_argument("--min-rvol", type=float, default=1)
    parser.add_argument("--min-volume", type=float, default=0)
    parser.add_argument("--min-price", type=float, default=0)
    parser.add_argument("--max-price", type=float, default=1e9)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--output", type=Path, help="Optional JSON result file")
    args = parser.parse_args()

    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    from database.auth_db import Auth, db_session
    from services.market_scanner_provider import ScannerError
    from services.market_scanner_service import scanner_manager

    try:
        username = args.username
        if not username:
            try:
                users = (
                    db_session.query(Auth.name)
                    .filter(Auth.broker == "fyers", Auth.is_revoked.is_(False))
                    .all()
                )
            finally:
                db_session.remove()
            if len(users) != 1:
                raise ScannerError(
                    "Log in to Fyers in OpenAlgo, then supply --username if multiple logins exist."
                )
            username = users[0][0]
        options = {
            key: getattr(args, key)
            for key in (
                "symbols",
                "lookback_days",
                "min_rvol",
                "min_volume",
                "min_price",
                "max_price",
                "limit",
            )
        }
        result, _ = scanner_manager.start(username, options)
        last_progress = None
        while result["state"] == "running":
            progress = (result["phase"], result["baselines_processed"], result["quotes_processed"])
            if progress != last_progress:
                print(
                    f"{result['phase']}: quotes {result['quotes_processed']}/{result['total']}; baselines {result['baselines_processed']}/{result['baseline_total']}",
                    flush=True,
                )
                last_progress = progress
            time.sleep(1)
            result = scanner_manager.results(username)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8"
            )
        print(
            f"\n{result['session_date']} IST | {result['state']} | {result['valid_quotes']}/{result['total']} valid quotes | partial={result['partial']}"
        )
        if result["error"]:
            print(result["error"])
        for title, key in (
            ("VOLUME SHOCKERS", "volume_shockers"),
            ("TOP GAINERS", "top_gainers"),
            ("TOP LOSERS", "top_losers"),
        ):
            print(
                f"\n{title}\n{'SYMBOL':<20} {'LTP':>12} {'CHANGE %':>10} {'VOLUME':>14} {'RVOL':>8}"
            )
            for row in result[key]:
                rvol = f"{row['rvol']:.2f}" if row["rvol"] is not None else "n/a"
                print(
                    f"{row['symbol']:<20} {row['ltp']:>12.2f} {row['change_percent']:>10.2f} {row['volume']:>14,.0f} {rvol:>8}"
                )
            if not result[key]:
                print("No matching stocks in the available quotes for today.")
        return 0 if result["state"] == "completed" else 1
    except ScannerError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        if "username" in locals() and username:
            scanner_manager.cancel(username)
        print("Scan cancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
