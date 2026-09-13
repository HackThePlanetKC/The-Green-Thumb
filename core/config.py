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
        "pairing_timeout_s": 60,           # BLE pairing scan window
    },
    "display": {
        "auto_cycle_interval_s": 10,       # time between automatic screen advances
        "resume_idle_s": 20,               # auto-cycle resumes 20s after a manual button press
        "night_mode": {
            "enabled": True,
            "start_hour": 22, "start_minute": 0,   # 24hr, local time (requires NTP sync)
            "end_hour": 7, "end_minute": 0,
            "led_off": False,   # also disable the WS2812 status LED during night mode - consumed by status_led.py
            "red_overrides_led_off": False,   # if true, an ESCALATED (blinking) red status shows even when led_off is set. Does NOT apply to plain solid red - only the post-delay escalated tier.
        },
    },
    "status_led": {
        "brightness": 0.15,   # 0.0-1.0 scalar applied to all colors - PLACEHOLDER, needs real tuning once the diffused-fingernail enclosure is physically built
        "blink_interval_ms": 500,
        "red_escalation_delay_s": 3600,   # how long red must persist before escalating to blinking + requires_immediate_attention flag. UX choice, not a sourced threshold - starting default, tune to preference
    },
    "timezone": {
        # Fixed UTC offset in hours (supports fractional, e.g. 5.5 for India).
        # MUST be set correctly by the user - MicroPython's NTP sync has no
        # timezone/DST database, so this is the only source of truth for
        # local time (used by night mode and daily light-hours rollover).
        # No automatic DST: if your region observes it, update this
        # manually twice a year.
        "utc_offset_hours": 0,
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
        # Raw values here are on the machine.ADC.read_u16() scale (0-65535,
        # portable across MicroPython ports), not raw 12-bit ADC counts.
        "min_delta_raw": 3277,  # placeholder, ~5% of 0-65535 range - retune once sensor datasheet is known
    },
    "light_calibration": {
        # Placeholder values only - not real measurements. The UI should
        # prompt for calibration during initial device setup. Do not
        # trust these values based on their presence alone; check
        # `calibrated` explicitly (see drivers/light_sensor.py).
        "dark_raw": 0,
        "bright_raw": 65535,
        "bright_fc": 500,
        "calibrated": False,
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


def get_by_path(config, path):
    """
    Reads a nested config value using dot notation, e.g.
    get_by_path(cfg, "thresholds.temp_f.green_min").

    Raises KeyError if any part of the path doesn't exist in the schema.
    Used by the generic MQTT set_config command handler (core/mqtt_client.py,
    not yet written) to validate paths before touching anything - an
    invalid/typo'd path should error, not silently no-op or create a new key.
    """
    node = config
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            raise KeyError(path)
        node = node[key]
    return node


def set_by_path(config, path, value):
    """
    Writes a nested config value using dot notation. Raises KeyError if
    the path (including the final key) doesn't already exist - this
    never creates new keys, only updates existing ones, so a malformed
    MQTT command can't silently inject arbitrary config structure.

    Does not save to flash - caller is responsible for calling save()
    after, so multiple set_by_path calls can be batched into one write.
    """
    keys = path.split(".")
    node = config
    for key in keys[:-1]:
        if not isinstance(node, dict) or key not in node:
            raise KeyError(path)
        node = node[key]
    last_key = keys[-1]
    if not isinstance(node, dict) or last_key not in node:
        raise KeyError(path)
    node[last_key] = value


def _copy(d):
    """Shallow recursive copy so callers can't accidentally mutate DEFAULT_CONFIG."""
    if isinstance(d, dict):
        return {k: _copy(v) for k, v in d.items()}
    return d
