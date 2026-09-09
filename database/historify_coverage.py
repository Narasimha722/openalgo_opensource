"""Broker-confirmed download windows, separate from candle min/max timestamps."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS historify_download_coverage (
    broker VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    exchange VARCHAR NOT NULL,
    interval VARCHAR NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    records BIGINT NOT NULL,
    checked_at TIMESTAMP DEFAULT current_timestamp,
    PRIMARY KEY (broker, symbol, exchange, interval, start_date, end_date)
)
"""


def ensure_coverage_table(conn):
    conn.execute(SCHEMA)


def get_coverage(conn, broker, symbol, exchange, interval):
    # Empty history is a provider observation, not proof of an IPO date.
    # Recheck it after a week in case the provider repairs its coverage.
    return conn.execute(
        """
        SELECT start_date, end_date FROM historify_download_coverage
        WHERE broker = ? AND symbol = ? AND exchange = ? AND interval = ?
          AND (records > 0 OR checked_at > current_timestamp - INTERVAL '7 days')
        ORDER BY start_date
        """,
        [broker, symbol.upper(), exchange.upper(), interval],
    ).fetchall()


def save_coverage(conn, broker, symbol, exchange, interval, start, end, records):
    conn.execute(
        """
        INSERT INTO historify_download_coverage
        (broker, symbol, exchange, interval, start_date, end_date, records)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (broker, symbol, exchange, interval, start_date, end_date)
        DO UPDATE SET records = EXCLUDED.records, checked_at = now()
        """,
        [broker, symbol.upper(), exchange.upper(), interval, start, end, records],
    )


def clear_coverage(conn, symbol, exchange, interval=None):
    # Deletion also works on installations that have not run the migration.
    exists = conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_name = 'historify_download_coverage'"
    ).fetchone()
    if not exists:
        return
    query = "DELETE FROM historify_download_coverage WHERE symbol = ? AND exchange = ?"
    params = [symbol.upper(), exchange.upper()]
    if interval:
        query += " AND interval = ?"
        params.append(interval)
    conn.execute(query, params)
