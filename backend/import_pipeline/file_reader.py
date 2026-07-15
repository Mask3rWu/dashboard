"""Pure text-file reading helpers used by the import pipeline."""

import re


ENCODINGS = ["gbk", "gb2312", "utf-8", "latin-1"]


def detect_encoding(filepath):
    """Detect text encoding of a file."""
    for encoding in ENCODINGS:
        try:
            with open(filepath, "r", encoding=encoding) as file:
                file.readline()
            return encoding
        except (UnicodeDecodeError, UnicodeError):
            continue
    return "latin-1"


def has_header(filepath):
    """Return whether the first non-empty token looks like a header."""
    encoding = detect_encoding(filepath)
    try:
        with open(filepath, "r", encoding=encoding, errors="replace") as file:
            first_line = file.readline().strip()
    except Exception:
        return False

    if not first_line:
        return False
    tokens = first_line.split()
    if not tokens:
        return False
    if tokens[0].strip().lower() == "time":
        return True
    if re.match(r"^\d{1,2}:\d{2}:\d{2}", tokens[0]):
        return False
    return True


def parse_lines(filepath):
    """Read a text file and return its non-empty stripped lines."""
    encoding = detect_encoding(filepath)
    with open(filepath, "r", encoding=encoding, errors="replace") as file:
        return [line.strip() for line in file.readlines() if line.strip()]
