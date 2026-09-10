"""Fyers-only market data access through OpenAlgo's shared rate limiter."""

from datetime import timedelta
from urllib.parse import urlencode


class ScannerError(Exception):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def get_fyers_token(user):
    from database.auth_db import db_session, get_auth_token, get_auth_token_dbquery

    try:
        record = get_auth_token_dbquery(user)
        if record is None or record.broker != "fyers":
            raise ScannerError("Log in to Fyers to use the stock scanner.", 403)
        token = get_auth_token(user, bypass_cache=True)
        if not token:
            raise ScannerError("Your Fyers session has expired. Log in again.", 401)
        return token
    finally:
        db_session.remove()


def load_universe():
    from database.symbol import SymToken, db_session

    try:
        records = (
            db_session.query(SymToken.symbol, SymToken.brsymbol, SymToken.name)
            .filter(SymToken.exchange == "NSE", SymToken.instrumenttype == "EQ")
            .order_by(SymToken.symbol)
            .all()
        )
        universe = {}
        for symbol, broker_symbol, name in records:
            if broker_symbol and broker_symbol.startswith("NSE:") and broker_symbol.endswith("-EQ"):
                universe[symbol] = {
                    "symbol": symbol,
                    "exchange": "NSE",
                    "name": name or symbol,
                    "broker_symbol": broker_symbol,
                }
        if not universe:
            raise ScannerError(
                "NSE equity symbols are unavailable. Refresh the Fyers master contract."
            )
        return list(universe.values())
    finally:
        db_session.remove()


class FyersScannerProvider:
    def __init__(self, token):
        self._token = token

    def _request(self, endpoint):
        from broker.fyers.api.data import get_api_response

        response = get_api_response(endpoint, self._token)
        if not isinstance(response, dict):
            raise ScannerError("Fyers returned an invalid market-data response.", 502)
        code = str(response.get("code", ""))
        if code in {"401", "403", "-8", "-15", "-16", "-17"}:
            raise ScannerError("Fyers authentication failed. Log in again.", 401)
        if code == "429":
            raise ScannerError("Fyers rate limit reached. Retry the scan later.", 429)
        if response.get("s") not in {"ok", "no_data"}:
            # Do not expose arbitrary broker messages or exception strings.
            raise ScannerError("Fyers could not provide market data.", 502)
        return response

    def quotes(self, instruments):
        if not 1 <= len(instruments) <= 50:
            raise ValueError("Fyers quote batches must contain 1 to 50 symbols")
        params = urlencode({"symbols": ",".join(item["broker_symbol"] for item in instruments)})
        response = self._request("/data/quotes?" + params)
        if response.get("s") != "ok" or not isinstance(response.get("d"), list):
            raise ScannerError("Fyers returned an invalid quotes response.", 502)
        return {
            item["n"]: item["v"]
            for item in response["d"]
            if isinstance(item, dict)
            and item.get("s") == "ok"
            and isinstance(item.get("n"), str)
            and isinstance(item.get("v"), dict)
        }

    def history(self, instrument, session_date):
        params = urlencode(
            {
                "symbol": instrument["broker_symbol"],
                "resolution": "1D",
                "date_format": "1",
                "range_from": (session_date - timedelta(days=120)).isoformat(),
                "range_to": (session_date - timedelta(days=1)).isoformat(),
                "cont_flag": "1",
            }
        )
        response = self._request("/data/history?" + params)
        candles = response.get("candles")
        if not isinstance(candles, list) or (response.get("s") == "no_data" and candles):
            raise ScannerError("Fyers returned an invalid daily-history response.", 502)
        return candles
