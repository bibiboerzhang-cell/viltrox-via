"""CLI entrypoint for promotion-plan Excel dry-runs."""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.integrations.excel_import.pipeline import format_dry_run_report, run_dry_run


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run parse a V-KPI promotion-plan workbook.")
    parser.add_argument("--xlsx", required=True, help="Path to the workbook.")
    args = parser.parse_args()

    path = Path(args.xlsx).expanduser()
    result = run_dry_run(path)
    print(format_dry_run_report(result))
    try:
        from app.db.connection import close_db_runtime

        asyncio.run(close_db_runtime())
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
