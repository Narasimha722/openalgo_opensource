"""Compare Historify's old pandas export with Polars on a repeated CSV workload.

No API or database access. Symbols are synthetic copies used only to scale the
export workload. Timings include conversion, metadata, concatenation, sorting and
writing, but exclude reading the source CSV and checking the resulting files.
"""

import argparse
import json
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path
from time import perf_counter

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from utils.historical_data_export import (
    EXPORT_COLUMNS,
    SORT_COLUMNS,
    prepare_export_frame,
    write_export_parquet,
)


def worker(args):
    raw = pd.read_csv(args.csv)
    if "timestamp" not in raw:
        stamp = pd.to_datetime(raw.date + " " + raw.time).dt.tz_localize("Asia/Kolkata")
        raw["timestamp"] = stamp.astype("int64") // 10**9
    raw = raw[["timestamp", "open", "high", "low", "close", "volume", "oi"]]
    started = perf_counter()
    frames = []
    for i in reversed(range(args.symbols)):
        symbol = f"TEST{i:04d}"
        if args.worker == "pandas":
            frame = raw.assign(symbol=symbol, exchange="NSE", interval="5m")
            frame["datetime"] = pd.to_datetime(frame.timestamp, unit="s")
            frames.append(frame[EXPORT_COLUMNS])
        else:
            frames.append(prepare_export_frame(raw, symbol, "NSE", "5m"))
    if args.worker == "pandas":
        combined = pd.concat(frames, ignore_index=True).sort_values(SORT_COLUMNS)
        combined.to_parquet(args.output, compression="zstd", index=False)
    else:
        write_export_parquet(frames, args.output)
    print(json.dumps({"seconds": perf_counter() - started, "rows": len(raw) * args.symbols}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--symbols", type=int, default=50)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", choices=["pandas", "polars"])
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return
    if args.symbols < 1 or args.repeats < 1:
        parser.error("symbols and repeats must be positive")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    timings = {"pandas": [], "polars": []}
    with tempfile.TemporaryDirectory(dir=args.output.parent) as directory:
        directory = Path(directory)
        for repeat in range(args.repeats):
            order = ["pandas", "polars"] if repeat % 2 == 0 else ["polars", "pandas"]
            for engine in order:
                command = [
                    sys.executable,
                    str(Path(__file__)),
                    "--csv",
                    str(args.csv),
                    "--symbols",
                    str(args.symbols),
                    "--output",
                    str(directory / f"{engine}.parquet"),
                    "--worker",
                    engine,
                ]
                result = subprocess.run(command, capture_output=True, text=True, check=True)
                record = json.loads(result.stdout)
                timings[engine].append(record["seconds"])
        pd.testing.assert_frame_equal(
            pd.read_parquet(directory / "pandas.parquet"),
            pd.read_parquet(directory / "polars.parquet"),
        )
    old = statistics.median(timings["pandas"])
    new = statistics.median(timings["polars"])
    report = {
        "rows": record["rows"],
        "synthetic_symbols": args.symbols,
        "seconds": timings,
        "median_pandas_seconds": old,
        "median_polars_seconds": new,
        "speedup": old / new,
        "validation": "PASS: exported values, order, columns and pandas dtypes identical",
        "scope": "Metadata, conversion, concat, sort and ZSTD Parquet write; excludes source CSV read and database queries. Fresh processes, alternating engine order. Synthetic symbol copies; no memory measurement.",
    }
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
