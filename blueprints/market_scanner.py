"""Session-authenticated scanner APIs; the UI is intentionally deferred."""

from functools import wraps

from flask import Blueprint, jsonify, request, session

from services.market_scanner_provider import ScannerError, get_fyers_token, load_universe
from services.market_scanner_service import scanner_manager
from utils.logging import get_logger
from utils.session import is_session_valid

logger = get_logger(__name__)
market_scanner_bp = Blueprint("market_scanner", __name__, url_prefix="/market-scanner/api")


def scanner_endpoint(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            if not is_session_valid() or not session.get("user"):
                raise ScannerError("Log in to OpenAlgo to use the scanner.", 401)
            if session.get("broker") != "fyers":
                raise ScannerError("The stock scanner requires a Fyers broker session.", 403)
            get_fyers_token(session["user"])
            return function(*args, **kwargs)
        except ScannerError as exc:
            return jsonify(status="error", message=str(exc)), exc.status_code
        except Exception:
            logger.exception("Stock scanner API failed")
            return jsonify(status="error", message="Unable to process the scanner request."), 500

    return wrapped


@market_scanner_bp.after_request
def no_cache(response):
    response.headers["Cache-Control"] = "no-store"
    return response


@market_scanner_bp.get("/universe")
@scanner_endpoint
def universe():
    items = load_universe()
    return jsonify(
        status="success",
        data={"exchange": "NSE", "series": "EQ", "count": len(items), "symbols": items},
    )


@market_scanner_bp.post("/scan")
@scanner_endpoint
def start_scan():
    data = request.get_json(silent=True)
    result, reused = scanner_manager.start(session["user"], data)
    return jsonify(status="success", data=result, reused=reused), 202


@market_scanner_bp.get("/results")
@scanner_endpoint
def results():
    return jsonify(
        status="success", data=scanner_manager.results(session["user"], request.args.to_dict())
    )


@market_scanner_bp.post("/cancel")
@scanner_endpoint
def cancel():
    return jsonify(status="success", data=scanner_manager.cancel(session["user"]))
