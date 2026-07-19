"""Bounded cleanup for generated synchronization artifacts."""

from __future__ import annotations

import os
import time


def cleanup_files(
    directory: str,
    *,
    max_age_seconds: float,
    suffixes: tuple[str, ...],
    keep_paths: tuple[str, ...] = (),
) -> dict[str, int]:
    root = os.path.abspath(directory)
    if not os.path.isdir(root):
        return {"removed_files": 0, "removed_bytes": 0}
    keep = {os.path.abspath(path) for path in keep_paths}
    cutoff = time.time() - max(0.0, float(max_age_seconds))
    removed_files = 0
    removed_bytes = 0
    for entry in os.scandir(root):
        if not entry.is_file(follow_symlinks=False):
            continue
        path = os.path.abspath(entry.path)
        if os.path.commonpath([path, root]) != root or path in keep:
            continue
        if suffixes and not entry.name.lower().endswith(tuple(value.lower() for value in suffixes)):
            continue
        try:
            stat = entry.stat(follow_symlinks=False)
            if stat.st_mtime >= cutoff:
                continue
            os.remove(path)
            removed_files += 1
            removed_bytes += int(stat.st_size)
        except OSError:
            continue
    return {"removed_files": removed_files, "removed_bytes": removed_bytes}
