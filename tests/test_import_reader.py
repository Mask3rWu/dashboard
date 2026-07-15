from __future__ import annotations

from backend.import_pipeline.file_reader import detect_encoding, has_header, parse_lines
from backend.import_pipeline.session_metadata import _extract_flight_date
from backend.import_pipeline.importer import time_to_sec as importer_time_to_sec
from backend.import_pipeline.scanner import (
    _build_clusters,
    parse_session_key,
    time_to_sec as scanner_time_to_sec,
)


def test_text_reader_current_encoding_and_header_behavior(tmp_path):
    utf8 = tmp_path / "utf8.txt"
    utf8.write_text("Time\tName\n00:00:01\tvalue\n", encoding="utf-8")
    bom = tmp_path / "bom.txt"
    bom.write_text("Time\tValue\n00:00:02\t2\n", encoding="utf-8-sig")
    gbk = tmp_path / "gbk.txt"
    gbk.write_bytes("Time\tName\n00:00:03\tvalue\n".encode("gbk"))

    # ASCII-compatible samples currently resolve to the first configured codec.
    assert detect_encoding(utf8) == "gbk"
    assert detect_encoding(bom) == "gbk"
    assert detect_encoding(gbk) == "gbk"
    assert has_header(utf8) is True
    assert has_header(bom) is True


def test_parse_lines_preserves_data_and_drops_blank_lines(tmp_path):
    with_header = tmp_path / "with_header.txt"
    with_header.write_text("Time Value\n\n00:00:01 1\ninvalid value\n", encoding="ascii")
    without_header = tmp_path / "without_header.txt"
    without_header.write_text("00:00:01 1\n00:00:02 nope\n", encoding="ascii")

    assert parse_lines(with_header) == ["Time Value", "00:00:01 1", "invalid value"]
    assert parse_lines(without_header) == ["00:00:01 1", "00:00:02 nope"]
    assert has_header(with_header) is True
    assert has_header(without_header) is False


def test_time_conversion_current_behavior():
    values = ["01:02:03.5", "00:00:00", "not-a-time", "", "25:10:02"]
    assert [scanner_time_to_sec(value) for value in values] == [
        importer_time_to_sec(value) for value in values
    ]
    assert scanner_time_to_sec("01:02:03.5") == 3723.5
    assert scanner_time_to_sec("not-a-time") == 0.0


def test_session_key_clustering_and_date_metadata(tmp_path):
    source_path = tmp_path / "20260715-flight" / "AC01"
    source_path.mkdir(parents=True)
    config = {"data_types": {"state": {"file_patterns": ["DroneStateData"]}}}

    first_key = parse_session_key("DroneStateData_115959.txt", config)
    second_key = parse_session_key("DroneStateData_120001_2.txt", config)
    clusters = _build_clusters(
        [
            {"session_key": first_key, "filename": "DroneStateData_115959.txt"},
            {"session_key": second_key, "filename": "DroneStateData_120001_2.txt"},
        ]
    )

    assert first_key == "115959"
    assert second_key == "120001_2"
    assert [(key, len(files)) for key, files in clusters] == [("120001_2", 2)]
    assert _extract_flight_date(source_path) == "2026-07-15"
