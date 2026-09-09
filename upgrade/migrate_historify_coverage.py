"""Add restart-safe Historify download coverage without altering existing candles."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.historify_coverage import ensure_coverage_table
from database.historify_db import get_connection, get_db_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    if not Path(get_db_path()).exists():
        print("Historify database does not exist; coverage will be created on initialization.")
        return
    with get_connection() as conn:
        exists = conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = 'historify_download_coverage'"
        ).fetchone()
        if args.status:
            print("Coverage table is present." if exists else "Coverage migration is pending.")
        else:
            ensure_coverage_table(conn)
            print("Historify coverage migration complete. Existing candles preserved.")


if __name__ == "__main__":
    main()
