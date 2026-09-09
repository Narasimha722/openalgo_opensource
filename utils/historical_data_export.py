"""Polars preparation and Parquet output for Historify's multi-symbol exports."""

from pathlib import Path

import pandas as pd
import polars as pl

EXPORT_COLUMNS = [
    "symbol",
    "exchange",
    "interval",
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "oi",
    "datetime",
]
SORT_COLUMNS = ["symbol", "exchange", "interval", "timestamp"]


def prepare_export_frame(
    candles: pd.DataFrame, symbol: str, exchange: str, interval: str
) -> pl.DataFrame:
    """Convert each query result once, before attaching repeated string columns.

    Keep the existing export contract: epoch seconds and a timezone-naive UTC
    datetime with nanosecond precision. Session aggregation remains in DuckDB.
    """
    return (
        pl.from_pandas(candles, include_index=False)
        .with_columns(
            pl.lit(symbol).alias("symbol"),
            pl.lit(exchange).alias("exchange"),
            pl.lit(interval).alias("interval"),
            pl.from_epoch("timestamp", time_unit="s").cast(pl.Datetime("ns")).alias("datetime"),
        )
        .select(EXPORT_COLUMNS)
    )


def write_export_parquet(
    frames: list[pl.DataFrame], output_path: str | Path, compression: str = "zstd"
) -> int:
    """Sort and write without constructing a combined pandas DataFrame.

    The sink streams its output, but the per-symbol query results are already in
    memory. This does not make the entire export an out-of-core operation.
    """
    if not frames:
        raise ValueError("No frames to export")
    codec = "uncompressed" if compression == "none" else compression
    (
        pl.concat([frame.lazy() for frame in frames], how="vertical_relaxed")
        .sort(SORT_COLUMNS)
        .sink_parquet(output_path, compression=codec, maintain_order=True)
    )
    return sum(frame.height for frame in frames)
