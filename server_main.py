"""Start the Flight Analyzer collaboration server from flight_analyzer.ini."""

from __future__ import annotations

import uvicorn

from backend.config import load_app_config, server_host, server_port


def main() -> None:
    load_app_config()
    uvicorn.run("server_app:app", host=server_host(), port=server_port(), log_level="info")


if __name__ == "__main__":
    main()
