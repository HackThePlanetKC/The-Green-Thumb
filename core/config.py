"""
config.py - GreenThumb base board configuration.

Loads/saves persistent config (thresholds, wifi, mqtt, calibration, timing)
to flash via storage.py. New keys added in future firmware versions are
merged onto existing saved configs on load, so an old config.json on disk
never breaks after an OTA update - missing keys just fall back to default.
"""

import storage

CONFIG_PATH = "/config.json"

# --- Defaults, as decided in the design phase ---
# Every threshold value below is either sourced (see project notes) or
# explicitly flagged null where no sourced value exists yet. Do not fill
# in a null with a guessed number here - set it via config once sourced.
DEFAULT_CONFIG = {
    "wifi": {
        "ssid": "",
        "password": "",
    },
    "mqtt": {
        "broker": "",
        "port": 1883,
        "username": "",
        "password": "",
    },
    "timing": {
        "sample_interval_s": 120,          # local sensor sampling: 2 min
        "publish_interval_s": 1800,        # MQTT state publish: 30 min default, adjustable at runtime
        "display_resume_idle_s": 20,       # auto-cycle resumes 20s after a manual button press
        "pairing_timeout_s": 60,           # BLE pairing scan window
    },
    "thresholds": {
        "temp_f": {
            "green_min": 65, "green_max": 75,
            "red_min": 50, "red_max": 90,
        },
        "humidity_pct": {
            "green_min": 40, "green_max": 60,
            "red_min": 20, "red_max": 80,
        },
        "light_fc": {
            "green_min": 200, "green_max": 500,
            "red_min": None, "red_max": None,  # unsourced - do not guess, see project notes
        },
        "soil_moisture_pct": {
            "green_min": 20, "green_max": 40,
            "red_min": 10, "red_max": 50,
        },
    },
    "light_tracking": {
        "present_threshold_fc": 75,
        "hours_target": {"min": 12, "max": 16},
        "high_intensity_hours_target": {"max": None},  # blocked on light_fc.red_max
    },
    "soil_calibration": {
        "dry_raw": None,
        "wet_raw": None,
        "min_delta_raw": 200,  # placeholder, ~5% of 12-bit ADC range - retune once sensor datasheet is known
    },
    "profile_name": "generic_houseplant",
}


def _deep_merge_defaults(loaded, defaults):
    """
    Fill in any keys missing from `loaded` using `defaults`, recursively.
    Values present in `loaded` are never overwritten - this only adds
    what's missing, so user-set thresholds and calibration survive
    firmware updates that add new config keys.
    """
    for key, default_value in defaults.items():
        if key not in loaded:
            loaded[key] = default_value
        elif isinstance(default_value, dict) and isinstance(loaded[key], dict):
            _deep_merge_defaults(loaded[key], default_value)
    return loaded


def load():
    """
    Load config from flash, merged onto defaults for any missing keys.
    If no config file exists yet (first boot), returns a fresh copy of
    DEFAULT_CONFIG.
    """
    loaded = storage.read_json(CONFIG_PATH)
    if loaded is None:
        return _copy(DEFAULT_CONFIG)
    return _deep_merge_defaults(loaded, DEFAULT_CONFIG)


def save(config):
    """Persist config to flash. Returns True/False - see storage.write_json."""
    return storage.write_json(CONFIG_PATH, config)


def _copy(d):
    """Shallow recursive copy so callers can't accidentally mutate DEFAULT_CONFIG."""
    if isinstance(d, dict):
        return {k: _copy(v) for k, v in d.items()}
    return d
