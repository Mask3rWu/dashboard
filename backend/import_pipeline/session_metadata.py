"""Pure session-key and source-path metadata helpers."""

import os


def _extract_flight_date(source_path):
    """Extract a YYYY-MM-DD date from an ancestor's YYYYMMDD prefix."""
    path = os.path.normpath(source_path)
    while True:
        dirname = os.path.basename(path)
        if len(dirname) >= 8 and dirname[:8].isdigit():
            date_text = dirname[:8]
            return f"{date_text[:4]}-{date_text[4:6]}-{date_text[6:8]}"
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return None


def _extract_timestamp_from_key(session_key):
    """Extract seconds since midnight from a session key's HHMMSS prefix."""
    timestamp = session_key[:6]
    if len(timestamp) == 6 and timestamp.isdigit():
        return int(timestamp[:2]) * 3600 + int(timestamp[2:4]) * 60 + int(timestamp[4:6])
    return None
