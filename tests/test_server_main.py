from __future__ import annotations

import server_main


def test_check_config_does_not_connect_to_mysql(tmp_path, monkeypatch, capsys):
    config_path = tmp_path / "flight_analyzer.ini"
    data_dir = tmp_path / "server-data"
    config_path.write_text(
        "\n".join(
            [
                "[server]",
                "host = 127.0.0.1",
                "port = 19000",
                f"data_dir = {data_dir}",
                "",
                "[mysql]",
                "host = 127.0.0.1",
                "database = unused_by_check",
                "user = unused_by_check",
                "password = unused_by_check",
            ]
        ),
        encoding="utf-8",
    )
    for name in ("SERVER_HOST", "SERVER_PORT", "SERVER_DATA_DIR", "SERVER_DB_URL"):
        monkeypatch.delenv(name, raising=False)

    server_main.main(["--config", str(config_path), "--check-config"])

    output = capsys.readouterr().out
    assert f"config={config_path.resolve()}" in output
    assert "listen=127.0.0.1:19000" in output
    assert f"data_dir={data_dir.resolve()}" in output


def test_check_runtime_loads_mysql_driver_without_connecting(tmp_path, monkeypatch, capsys):
    config_path = tmp_path / "flight_analyzer.ini"
    config_path.write_text(
        "\n".join(
            [
                "[server]",
                "host = 127.0.0.1",
                "port = 19001",
                "",
                "[mysql]",
                "host = 127.0.0.1",
                "port = 1",
                "database = runtime_check",
                "user = runtime_check",
                "password = runtime_check",
            ]
        ),
        encoding="utf-8",
    )
    for name in ("SERVER_HOST", "SERVER_PORT", "SERVER_DATA_DIR", "SERVER_DB_URL"):
        monkeypatch.delenv(name, raising=False)

    server_main.main(["--config", str(config_path), "--check-runtime"])

    output = capsys.readouterr().out
    assert "database_driver=mysql+pymysql" in output
    assert "api_routes=" in output
