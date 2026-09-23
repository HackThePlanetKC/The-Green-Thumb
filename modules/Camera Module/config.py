"""
config.py - Camera Module configuration (Pi/Linux side).

Same load/merge/save idiom as the base station's core/config.py, for
consistency: DEFAULT_CONFIG below is deep-merged onto whatever's
already saved, so a config.json from an older module version never
breaks after an update - missing keys just fall back to default (see
_deep_merge_defaults). Existing values are never overwritten by this
merge, only filled in where absent.

This module's config is entirely separate from the base station's -
different filesystem, different platform, no shared config.json and
no code path that reads/writes the other's file. See
docs/ARCHITECTURE.md's modules/ layout note for why.

Atomic writes (temp file + os.replace) for the same reason as the
base's storage.py: a power loss or crash mid-write should never leave
a half-written, unparseable config.json behind.
"""

import json
import os

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

DEFAULT_CONFIG = {
    "flash": {
        # Below this reading, maybe_flash() fires the ring before
        # capture (see flash_controller.py). Same units this module's
        # light sensor driver reports (not written yet - see README.md
        # "Remaining"). Sane starting default, not sourced from a
        # datasheet - like the base station's own thresholds, this is
        # meant to be tuned once mounted, not treated as fixed. See
        # this module's BUILD.md.
        "low_light_threshold": 50,
        # W-channel brightness while flashing, 0.0-1.0 - independent of
        # the base station's status_led.brightness (different LED
        # hardware entirely), see ring.py.
        "brightness": 0.5,
    },
}


def _deep_merge_defaults(loaded, defaults):
    """
    Fill in any keys missing from `loaded` using `defaults`, recursively.
    Values present in `loaded` are never overwritten - this only adds
    what's missing, mirroring core/config.py's own function of the same
    name and purpose.
    """
    for key, default_value in defaults.items():
        if key not in loaded:
            loaded[key] = default_value
        elif isinstance(default_value, dict) and isinstance(loaded[key], dict):
            _deep_merge_defaults(loaded[key], default_value)
    return loaded


def load(path=CONFIG_PATH):
    """
    Load config from disk, merged onto defaults for any missing keys.
    If no config file exists yet (first run), returns a fresh copy of
    DEFAULT_CONFIG. A corrupt/unparseable file is treated the same as
    "doesn't exist" - this never guesses at a partial value, matching
    core/storage.py's read_json() behavior.
    """
    try:
        with open(path) as f:
            loaded = json.load(f)
    except (OSError, ValueError):
        return _copy(DEFAULT_CONFIG)
    return _deep_merge_defaults(loaded, DEFAULT_CONFIG)


def save(config, path=CONFIG_PATH):
    """Atomically persist config to disk (temp file + os.replace)."""
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(config, f)
    os.replace(tmp_path, path)


def _copy(d):
    """Shallow recursive copy so callers can't accidentally mutate DEFAULT_CONFIG."""
    if isinstance(d, dict):
        return {k: _copy(v) for k, v in d.items()}
    return d
