"""
storage.py - Generic flash-backed key storage with atomic writes.

MicroPython's flash filesystem can be corrupted by power loss mid-write.
All writes here go to a temp file first, then os.rename() over the target -
rename is atomic on littlefs/FAT, so a power loss during write leaves the
original file intact instead of a half-written, unparseable one.
"""

import os

try:
    import ujson as json
except ImportError:
    import json


def read_json(path):
    """
    Read and parse a JSON file. Returns None if the file doesn't exist
    or contains invalid JSON (caller decides fallback behavior - this
    module never silently invents defaults).
    """
    try:
        with open(path, "r") as f:
            return json.load(f)
    except OSError:
        return None
    except ValueError:
        # Corrupt/partial JSON on disk. Caller must handle this -
        # storage.py does not guess what the data should have been.
        return None


def write_json(path, data):
    """
    Atomically write data as JSON to path.
    Returns True on success, False on failure (caller should retry/alert -
    this does not raise, since a firmware crash on a flash write is worse
    than a caller checking a return value).
    """
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w") as f:
            json.dump(data, f)
        os.rename(tmp_path, path)
        return True
    except OSError:
        _cleanup_tmp(tmp_path)
        return False


def delete(path):
    """Remove a file if it exists. Returns True if removed, False if it didn't exist."""
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def _cleanup_tmp(tmp_path):
    try:
        os.remove(tmp_path)
    except OSError:
        pass
