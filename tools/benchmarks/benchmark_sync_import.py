"""Benchmark parsed-row exchange and MySQL ingestion strategies.

The benchmark only writes randomly named ``sync_benchmark_*`` tables and drops
them after each run. Pass ``--confirm-write`` explicitly to enable database I/O.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import random
import shutil
import statistics
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence


DEFAULT_BATCH_SIZES = (1000, 5000)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def benchmark_columns(value_columns: int) -> list[str]:
    return ["flight_id", "time_str", "time_sec", *[f"value_{i}" for i in range(value_columns)], "note"]


def row_values(row_index: int, value_columns: int) -> tuple[Any, ...]:
    return (
        1 + row_index // 100_000,
        f"{row_index // 360000:02d}:{(row_index // 6000) % 60:02d}:{(row_index // 100) % 60:02d}.{row_index % 100:02d}",
        row_index / 100.0,
        *(((row_index * (column + 3)) % 100_003) / 100.0 for column in range(value_columns)),
        f"sample-{row_index % 97}",
    )


def iter_rows(row_count: int, value_columns: int) -> Iterator[tuple[Any, ...]]:
    for row_index in range(row_count):
        yield row_values(row_index, value_columns)


def batched(rows: Iterable[tuple[Any, ...]], batch_size: int) -> Iterator[list[tuple[Any, ...]]]:
    batch: list[tuple[Any, ...]] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch


def tsv_cell(value: Any) -> str:
    if value is None:
        return r"\N"
    text = str(value)
    replacements = (
        ("\\", r"\\"),
        ("\0", r"\0"),
        ("\b", r"\b"),
        ("\n", r"\n"),
        ("\r", r"\r"),
        ("\t", r"\t"),
        ("\x1a", r"\Z"),
    )
    for source, escaped in replacements:
        text = text.replace(source, escaped)
    return text


def write_tsv(path: str, row_count: int, value_columns: int) -> dict[str, Any]:
    started = time.perf_counter()
    with open(path, "w", encoding="utf-8", newline="\n") as output:
        for row in iter_rows(row_count, value_columns):
            output.write("\t".join(tsv_cell(value) for value in row))
            output.write("\n")
    return {
        "seconds": time.perf_counter() - started,
        "bytes": os.path.getsize(path),
        "rows": row_count,
    }


def rows_for_target_bytes(target_bytes: int, value_columns: int) -> int:
    sample_rows = 1000
    sample_bytes = sum(
        len(("\t".join(tsv_cell(value) for value in row) + "\n").encode("utf-8"))
        for row in iter_rows(sample_rows, value_columns)
    )
    return max(1, math.ceil(target_bytes / (sample_bytes / sample_rows)))


def capacity_disk_preflight(target_bytes: int, mysql_data_dir: str | None) -> dict[str, Any]:
    temp_dir = tempfile.gettempdir()
    checks = [
        {
            "name": "temporary_files",
            "path": temp_dir,
            "required_bytes": math.ceil(target_bytes * 1.2),
            "free_bytes": shutil.disk_usage(temp_dir).free,
        }
    ]
    if mysql_data_dir and os.path.isdir(mysql_data_dir):
        checks.append(
            {
                "name": "mysql_data",
                "path": os.path.abspath(mysql_data_dir),
                "required_bytes": math.ceil(target_bytes * 3.0),
                "free_bytes": shutil.disk_usage(mysql_data_dir).free,
            }
        )
    for check in checks:
        check["ok"] = check["free_bytes"] >= check["required_bytes"]
    return {"ok": all(check["ok"] for check in checks), "checks": checks}


def _table_ddl(db, table_name: str, value_columns: int) -> str:
    value_sql = ", ".join(f"{db.quote_identifier(f'value_{i}')} DOUBLE NULL" for i in range(value_columns))
    return f"""
        CREATE TABLE {db.quote_identifier(table_name)} (
            id BIGINT PRIMARY KEY AUTO_INCREMENT,
            flight_id BIGINT NOT NULL,
            time_str VARCHAR(64) NULL,
            time_sec DOUBLE NULL,
            {value_sql},
            note TEXT NULL,
            INDEX idx_flight_time (flight_id, time_sec),
            INDEX idx_flight_id (flight_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """


def _create_table(engine, db, table_name: str, value_columns: int) -> None:
    with engine.begin() as conn:
        conn.execute(db.text(_table_ddl(db, table_name, value_columns)))


def _drop_table(engine, db, table_name: str) -> None:
    with engine.begin() as conn:
        conn.execute(db.text(f"DROP TABLE IF EXISTS {db.quote_identifier(table_name)}"))


def benchmark_insert(engine, db, row_count: int, value_columns: int, batch_size: int) -> dict[str, Any]:
    table_name = f"sync_benchmark_insert_{uuid.uuid4().hex[:12]}"
    columns = benchmark_columns(value_columns)
    _create_table(engine, db, table_name, value_columns)
    placeholders = ", ".join(f":p{i}" for i in range(len(columns)))
    statement = db.text(
        f"INSERT INTO {db.quote_identifier(table_name)} "
        f"({', '.join(db.quote_identifier(column) for column in columns)}) "
        f"VALUES ({placeholders})"
    )
    started = time.perf_counter()
    try:
        with engine.begin() as conn:
            for rows in batched(iter_rows(row_count, value_columns), batch_size):
                conn.execute(
                    statement,
                    [
                        {f"p{index}": value for index, value in enumerate(row)}
                        for row in rows
                    ],
                )
        seconds = time.perf_counter() - started
        return {
            "method": "sqlalchemy_executemany",
            "batch_size": batch_size,
            "rows": row_count,
            "seconds": seconds,
            "rows_per_second": row_count / seconds if seconds else None,
        }
    finally:
        _drop_table(engine, db, table_name)


def benchmark_load_data(engine, db, tsv_path: str, row_count: int, value_columns: int) -> dict[str, Any]:
    table_name = f"sync_benchmark_load_{uuid.uuid4().hex[:12]}"
    columns = benchmark_columns(value_columns)
    _create_table(engine, db, table_name, value_columns)
    sql = (
        f"LOAD DATA LOCAL INFILE %s INTO TABLE {db.quote_identifier(table_name)} "
        "CHARACTER SET utf8mb4 FIELDS TERMINATED BY '\\t' ESCAPED BY '\\\\' "
        "LINES TERMINATED BY '\\n' "
        f"({', '.join(db.quote_identifier(column) for column in columns)})"
    )
    started = time.perf_counter()
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(sql, (os.path.abspath(tsv_path),))
        seconds = time.perf_counter() - started
        return {
            "method": "mysql_load_data_local",
            "rows": row_count,
            "seconds": seconds,
            "rows_per_second": row_count / seconds if seconds else None,
        }
    finally:
        _drop_table(engine, db, table_name)


def benchmark_arrow_codecs(directory: str, row_count: int, value_columns: int, batch_size: int) -> list[dict[str, Any]]:
    try:
        import pyarrow as pa
        import pyarrow.ipc as ipc
        import pyarrow.parquet as parquet
    except ImportError:
        return [{"method": "arrow_parquet", "status": "unavailable", "reason": "pyarrow is not installed"}]

    columns = benchmark_columns(value_columns)
    schema = pa.schema(
        [
            pa.field("flight_id", pa.int64()),
            pa.field("time_str", pa.string()),
            pa.field("time_sec", pa.float64()),
            *[pa.field(f"value_{i}", pa.float64()) for i in range(value_columns)],
            pa.field("note", pa.string()),
        ]
    )
    paths = {
        "arrow_ipc": os.path.join(directory, "benchmark.arrow"),
        "parquet": os.path.join(directory, "benchmark.parquet"),
    }
    results = []
    for method, path in paths.items():
        started = time.perf_counter()
        writer = (
            ipc.new_file(path, schema)
            if method == "arrow_ipc"
            else parquet.ParquetWriter(path, schema, compression="zstd")
        )
        try:
            for rows in batched(iter_rows(row_count, value_columns), batch_size):
                arrays = list(zip(*rows))
                batch = pa.RecordBatch.from_arrays(
                    [pa.array(values, type=field.type) for values, field in zip(arrays, schema)],
                    schema=schema,
                )
                writer.write_batch(batch)
        finally:
            writer.close()
        encode_seconds = time.perf_counter() - started

        started = time.perf_counter()
        decoded_rows = 0
        if method == "arrow_ipc":
            with ipc.open_file(path) as reader:
                for index in range(reader.num_record_batches):
                    decoded_rows += reader.get_batch(index).num_rows
        else:
            parquet_file = parquet.ParquetFile(path)
            decoded_rows = sum(batch.num_rows for batch in parquet_file.iter_batches(batch_size=batch_size))
        decode_seconds = time.perf_counter() - started
        results.append(
            {
                "method": method,
                "rows": decoded_rows,
                "bytes": os.path.getsize(path),
                "encode_seconds": encode_seconds,
                "encode_rows_per_second": row_count / encode_seconds if encode_seconds else None,
                "decode_seconds": decode_seconds,
                "decode_rows_per_second": decoded_rows / decode_seconds if decode_seconds else None,
                "note": "Codec-only measurement; MySQL still requires INSERT or text bulk-load.",
            }
        )
    return results


def _parse_batch_sizes(value: str) -> tuple[int, ...]:
    sizes = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    if not sizes or any(size <= 0 for size in sizes):
        raise argparse.ArgumentTypeError("batch sizes must be positive comma-separated integers")
    return sizes


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=None, help="Optional flight_analyzer.ini path")
    parser.add_argument("--rows", type=int, default=100_000)
    parser.add_argument("--target-gb", type=float, default=None, help="Derive row count for this TSV payload size")
    parser.add_argument("--value-columns", type=int, default=24)
    parser.add_argument("--batch-sizes", type=_parse_batch_sizes, default=DEFAULT_BATCH_SIZES)
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--skip-load-data", action="store_true")
    parser.add_argument("--codecs", action="store_true", help="Also benchmark Arrow IPC and Parquet when pyarrow is installed")
    parser.add_argument("--output", default=None, help="Optional JSON result path")
    parser.add_argument("--allow-low-disk", action="store_true", help="Override target-gb disk preflight")
    parser.add_argument("--preflight-only", action="store_true", help="Read-only target size and disk check")
    parser.add_argument("--confirm-write", action="store_true", help="Required: create and drop isolated MySQL benchmark tables")
    return parser


def _emit_result(result: dict[str, Any], output_path_value: str | None) -> None:
    output = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    print(output)
    if output_path_value:
        output_path = Path(output_path_value).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.confirm_write and not args.preflight_only:
        raise SystemExit("Refusing database benchmark without --confirm-write")
    if args.preflight_only and args.target_gb is None:
        raise SystemExit("--preflight-only requires --target-gb")
    if (
        args.rows <= 0
        or args.value_columns <= 0
        or args.trials <= 0
        or (args.target_gb is not None and args.target_gb <= 0)
    ):
        raise SystemExit("rows, target-gb, value-columns, and trials must be positive")
    row_count = (
        rows_for_target_bytes(int(args.target_gb * 1024**3), args.value_columns)
        if args.target_gb is not None
        else args.rows
    )

    from backend.config import load_app_config

    config_path = load_app_config(args.config)
    from sqlalchemy import create_engine
    from backend import server_database as db

    engine = create_engine(
        db.SERVER_DB_URL,
        pool_pre_ping=True,
        future=True,
        connect_args={"local_infile": True},
    )
    result: dict[str, Any] = {
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "config": str(config_path) if config_path else None,
        },
        "workload": {
            "rows": row_count,
            "value_columns": args.value_columns,
            "target_gb": args.target_gb,
            "trials": args.trials,
        },
        "results": [],
    }
    try:
        with engine.connect() as conn:
            result["environment"]["mysql_version"] = conn.exec_driver_sql("SELECT VERSION()").scalar()
            result["environment"]["local_infile"] = conn.exec_driver_sql("SELECT @@local_infile").scalar()
            mysql_data_dir = conn.exec_driver_sql("SELECT @@datadir").scalar()
            result["environment"]["mysql_data_dir"] = mysql_data_dir
        if args.target_gb is not None:
            disk_preflight = capacity_disk_preflight(
                int(args.target_gb * 1024**3), mysql_data_dir
            )
            result["disk_preflight"] = disk_preflight
            if not disk_preflight["ok"] and not args.allow_low_disk:
                if not args.preflight_only:
                    failed = [check for check in disk_preflight["checks"] if not check["ok"]]
                    raise RuntimeError(
                        "Capacity benchmark disk preflight failed: "
                        + ", ".join(
                            f"{check['name']} requires {check['required_bytes']} bytes but has {check['free_bytes']}"
                            for check in failed
                        )
                    )
        if args.preflight_only:
            _emit_result(result, args.output)
            return 0
        with tempfile.TemporaryDirectory(prefix="flight_sync_benchmark_") as temp_dir:
            tsv_path = os.path.join(temp_dir, "source.tsv")
            tsv_result = write_tsv(tsv_path, row_count, args.value_columns)
            tsv_result.update(
                {
                    "method": "tsv_encode",
                    "rows_per_second": row_count / tsv_result["seconds"] if tsv_result["seconds"] else None,
                }
            )
            result["results"].append(tsv_result)
            insert_cases = [
                (trial, batch_size)
                for trial in range(1, args.trials + 1)
                for batch_size in args.batch_sizes
            ]
            random.Random(20260718).shuffle(insert_cases)
            for trial, batch_size in insert_cases:
                insert_result = benchmark_insert(
                    engine, db, row_count, args.value_columns, batch_size
                )
                insert_result["trial"] = trial
                result["results"].append(insert_result)
            if not args.skip_load_data:
                if result["environment"]["local_infile"]:
                    result["results"].append(
                        benchmark_load_data(engine, db, tsv_path, row_count, args.value_columns)
                    )
                else:
                    result["results"].append(
                        {
                            "method": "mysql_load_data_local",
                            "status": "unavailable",
                            "reason": "MySQL @@local_infile is disabled",
                        }
                    )
            if args.codecs:
                result["results"].extend(
                    benchmark_arrow_codecs(temp_dir, row_count, args.value_columns, max(args.batch_sizes))
                )
    finally:
        engine.dispose()

    result["insert_summary"] = []
    for batch_size in args.batch_sizes:
        samples = [
            item["rows_per_second"]
            for item in result["results"]
            if item.get("method") == "sqlalchemy_executemany"
            and item.get("batch_size") == batch_size
        ]
        result["insert_summary"].append(
            {
                "batch_size": batch_size,
                "trials": len(samples),
                "median_rows_per_second": statistics.median(samples),
                "min_rows_per_second": min(samples),
                "max_rows_per_second": max(samples),
            }
        )

    _emit_result(result, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
