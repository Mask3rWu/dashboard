"""Start the Flight Analyzer collaboration server from flight_analyzer.ini."""

from __future__ import annotations

import argparse
from typing import Sequence

import uvicorn

from backend.config import load_app_config, server_data_dir, server_host, server_port


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default=None,
        help="Optional flight_analyzer.ini path. Defaults to the EXE directory/config search order.",
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Print non-secret runtime settings and exit without connecting to MySQL.",
    )
    parser.add_argument(
        "--check-runtime",
        action="store_true",
        help="Load the packaged server and MySQL driver without opening a database connection.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    config_path = load_app_config(args.config)

    if args.check_config or args.check_runtime:
        print(f"config={config_path or '(not found)'}")
        print(f"listen={server_host()}:{server_port()}")
        print(f"data_dir={server_data_dir()}")
        if not args.check_runtime:
            return

        from backend import server_database as db
        from server_app import app

        engine = db.get_engine()
        try:
            print(f"database_driver={engine.url.drivername}")
            print(f"api_routes={len(app.routes)}")
        finally:
            engine.dispose()
        return

    # Import after configuration is loaded so frozen and source runs initialize
    # database module constants from the selected deployment file.
    from server_app import app

    uvicorn.run(app, host=server_host(), port=server_port(), log_level="info")


if __name__ == "__main__":
    main()
