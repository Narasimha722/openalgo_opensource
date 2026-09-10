"""Disposable daily volume baselines. Never stores broker credentials or quotes."""

import json
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import Column, MetaData, String, Table, Text, delete, select

from database.engine_factory import create_db_engine


class BaselineCache:
    def __init__(self, database_url=None):
        if database_url is None:
            path = Path(__file__).resolve().parents[1] / "db" / "market_scanner.db"
            path.parent.mkdir(parents=True, exist_ok=True)
            database_url = "sqlite:///" + path.as_posix()
        self.engine = create_db_engine(database_url)
        metadata = MetaData()
        self.table = Table(
            "fyers_volume_baselines",
            metadata,
            Column("symbol", String(100), primary_key=True),
            Column("session_date", String(10), primary_key=True),
            Column("candles", Text, nullable=False),
        )
        metadata.create_all(self.engine)

    def get(self, symbol, session_date):
        with self.engine.connect() as connection:
            raw = connection.execute(
                select(self.table.c.candles).where(
                    self.table.c.symbol == symbol,
                    self.table.c.session_date == session_date,
                )
            ).scalar_one_or_none()
        return json.loads(raw) if raw is not None else None

    def put(self, symbol, session_date, candles):
        # The scanner serializes jobs in this process. Replace in a transaction;
        # a second process can safely overwrite the same public market baseline.
        with self.engine.begin() as connection:
            connection.execute(
                delete(self.table).where(
                    self.table.c.symbol == symbol,
                    self.table.c.session_date == session_date,
                )
            )
            connection.execute(
                self.table.insert().values(
                    symbol=symbol,
                    session_date=session_date,
                    candles=json.dumps(candles, allow_nan=False),
                )
            )

    def prune(self, session_date):
        cutoff = (date.fromisoformat(session_date) - timedelta(days=7)).isoformat()
        with self.engine.begin() as connection:
            connection.execute(delete(self.table).where(self.table.c.session_date < cutoff))
