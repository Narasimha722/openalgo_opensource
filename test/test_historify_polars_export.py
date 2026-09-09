"""Check Parquet compatibility and the existing Historify interval contracts."""

from contextlib import contextmanager

import duckdb
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest

from database import historify_db
from utils.historical_data_export import (
    EXPORT_COLUMNS,
    prepare_export_frame,
    write_export_parquet,
)


def candles():
    return pd.DataFrame(
        {
            "timestamp": [1704080760, 1704080700, 1704080820],
            "open": [101.0, 100.0, 102.0],
            "high": [103.0, 102.0, 104.0],
            "low": [100.0, 99.0, 101.0],
            "close": [102.0, 101.0, 103.0],
            "volume": [100, 200, 0],
            "oi": [0, 1, 2],
        }
    )


def legacy_frame(data, symbol, exchange="NSE", interval="1m"):
    result = data.assign(symbol=symbol, exchange=exchange, interval=interval)
    result["datetime"] = pd.to_datetime(result.timestamp, unit="s")
    return result[EXPORT_COLUMNS]


@pytest.mark.parametrize("compression", ["zstd", "snappy", "gzip", "none"])
def test_export_roundtrip_matches_pandas(tmp_path, compression):
    source = candles()
    source.loc[0, "close"] = np.nan  # Match pandas/Arrow null handling too.
    frames = [prepare_export_frame(source, symbol, "NSE", "1m") for symbol in ["ZZZ", "AAA"]]
    output = tmp_path / "actual.parquet"
    assert write_export_parquet(frames, output, compression) == 6
    expected = pd.concat([legacy_frame(source, symbol) for symbol in ["ZZZ", "AAA"]])
    expected = expected.sort_values(["symbol", "exchange", "interval", "timestamp"]).reset_index(
        drop=True
    )
    pd.testing.assert_frame_equal(pd.read_parquet(output), expected)
    codec = pq.ParquetFile(output).metadata.row_group(0).column(0).compression
    assert codec == ("UNCOMPRESSED" if compression == "none" else compression.upper())


def test_mixed_numeric_schemas_match_concat(tmp_path):
    first = candles()
    second = candles().astype({"volume": float})
    second.loc[0, "volume"] = 250.5
    output = tmp_path / "mixed.parquet"
    write_export_parquet(
        [
            prepare_export_frame(first, "A", "NSE", "5m"),
            prepare_export_frame(second, "B", "NSE", "5m"),
        ],
        output,
    )
    expected = pd.concat(
        [legacy_frame(first, "A", interval="5m"), legacy_frame(second, "B", interval="5m")]
    )
    expected = expected.sort_values(["symbol", "exchange", "interval", "timestamp"]).reset_index(
        drop=True
    )
    pd.testing.assert_frame_equal(pd.read_parquet(output), expected)


@pytest.fixture
def database(monkeypatch, tmp_path):
    conn = duckdb.connect(":memory:")
    conn.execute(
        "CREATE TABLE market_data (symbol VARCHAR, exchange VARCHAR, interval VARCHAR, "
        "timestamp BIGINT, open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, volume BIGINT, oi BIGINT)"
    )
    conn.execute("CREATE TABLE data_catalog (symbol VARCHAR, exchange VARCHAR)")
    start = int(pd.Timestamp("2026-01-05 09:15", tz="Asia/Kolkata").timestamp())
    for symbol in ["ZZZ", "AAA"]:
        conn.execute("INSERT INTO data_catalog VALUES (?, 'NSE')", [symbol])
        conn.executemany(
            "INSERT INTO market_data VALUES (?, 'NSE', '1m', ?, ?, ?, ?, ?, ?, ?)",
            [
                (symbol, start + i * 60, 100.0 + i, 102.0 + i, 99.0 + i, 101.0 + i, 10 + i, i)
                for i in range(10)
            ],
        )
        conn.executemany(
            "INSERT INTO market_data VALUES (?, 'NSE', 'D', ?, ?, ?, ?, ?, ?, ?)",
            [
                (symbol, start + i * 86400, 100.0 + i, 102.0 + i, 99.0 + i, 101.0 + i, 100 + i, i)
                for i in range(3)
            ],
        )

    @contextmanager
    def connection():
        yield conn

    monkeypatch.setattr(historify_db, "get_connection", connection)
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    yield conn, start
    conn.close()


@pytest.mark.parametrize("interval, expected_count", [("1m", 20), ("D", 6), ("5m", 4), ("W", 2)])
def test_historify_export_all_interval_branches(database, tmp_path, interval, expected_count):
    output = tmp_path / f"{interval}.parquet"
    ok, message, count = historify_db.export_to_parquet(str(output), interval=interval)
    assert ok, message
    assert count == expected_count
    result = pd.read_parquet(output)
    assert result.columns.tolist() == EXPORT_COLUMNS
    assert result.interval.eq(interval).all()
    assert result.symbol.unique().tolist() == ["AAA", "ZZZ"]
    assert result.groupby("symbol").timestamp.apply(lambda x: x.is_monotonic_increasing).all()
    pd.testing.assert_series_equal(
        result.datetime, pd.to_datetime(result.timestamp, unit="s"), check_names=False
    )
    if interval == "5m":
        assert result.iloc[0][["open", "high", "low", "close", "volume", "oi"]].tolist() == [
            100.0,
            106.0,
            99.0,
            105.0,
            60.0,
            4.0,
        ]


def test_symbol_and_timestamp_filters(database, tmp_path):
    _, start = database
    output = tmp_path / "filtered.parquet"
    ok, message, count = historify_db.export_to_parquet(
        str(output),
        symbols=[{"symbol": "zzz", "exchange": "nse"}],
        interval="1m",
        start_timestamp=start + 120,
        end_timestamp=start + 180,
    )
    assert ok, message
    assert count == 2
    result = pd.read_parquet(output)
    assert result.symbol.tolist() == ["ZZZ", "ZZZ"]
    assert result.timestamp.tolist() == [start + 120, start + 180]


def test_empty_and_missing_source(database, tmp_path):
    output = tmp_path / "empty.parquet"
    ok, message, count = historify_db.export_to_parquet(
        str(output), symbols=[{"symbol": "MISSING", "exchange": "NSE"}], interval="5m"
    )
    assert not ok and count == 0
    assert "Missing source data" in message
    assert not output.exists()


def test_failed_write_removes_partial_file(database, tmp_path, monkeypatch):
    output = tmp_path / "failed.parquet"

    def fail(frames, path, compression):
        output.write_bytes(b"partial")
        raise OSError("test write failure")

    monkeypatch.setattr("utils.historical_data_export.write_export_parquet", fail)
    ok, message, count = historify_db.export_to_parquet(str(output), interval="1m")
    assert not ok and count == 0
    assert "test write failure" in message
    assert not output.exists()
