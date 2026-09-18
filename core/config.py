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
    "device_name": "Green Thumb",  # user-configurable, editable from the dashboard - see web/server.py
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
    "sensor_failure": {
        # Currently tracked for DHT11 unconditionally (it has no
        # calibration concept - a None reading always means a genuine
        # read failure), and for soil moisture / light ONLY once their
        # own calibration is complete - see main.py's
        # _update_sensor_failure_tracking. Before calibration, a None
        # reading from those two is an expected, normal state, not a
        # failure; conflating the two would nag a new user about
        # "sensor failure" for a sensor they simply haven't calibrated
        # yet. Once calibrated, a None reading from soil/light would be
        # genuinely unexpected (worth noting: read_percent()/read_fc()
        # have no other None-producing path once truly calibrated, so
        # this mostly serves as a consistency/safety-net mechanism for
        # those two today, not a robust ADC-failure detector - it's
        # wired the same way as DHT11 for uniformity, not because a new
        # failure-detection heuristic was invented for ADC sensors).
        "alert_after_s": 3600,             # 1h continuous failure: visible in health/dashboard, no physical LED yet
        "notify_after_s": 21600,           # 6h continuous failure: escalates to the physical alert LED
        # When 2+ sensors are failing at once: "combined" (default)
        # aggregates them into a single dashboard/health entry without
        # changing any timing - each sensor is still tracked and times
        # out independently, they're just displayed together once
        # multiple are simultaneously alerting. "layered" instead
        # divides both thresholds above by the number of CURRENTLY
        # failing sensors, so the alert/notify tiers arrive
        # progressively sooner the more things are wrong at once (2
        # concurrent failures halves both thresholds, 3 divides by 3,
        # etc.) - a stronger, faster signal that something is
        # systemically wrong (e.g. a wiring/power issue affecting
        # multiple sensors), not just one isolated sensor acting up.
        "multi_alert_mode": "combined",    # "combined" | "layered"
    },
    "display": {
        "auto_cycle_interval_s": 10,       # time between automatic screen advances
        "resume_idle_s": 20,               # auto-cycle resumes 20s after a manual button press
        # Display-only preference (OLED + dashboard live reading) - does
        # NOT affect thresholds.temp_f (still always °F internally) or
        # any stored/calibration value. Converting stored thresholds
        # too would mean a repeated F->C->F edit cycle could drift a
        # real health-relevant number by rounding - not worth the risk
        # for a presentation preference. See docs/ARCHITECTURE.md.
        "temp_unit": "F",                  # "F" | "C"
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
        # How the LED shows plain (non-escalated, non-alert) health status:
        # "solid" (default) - steady color, no motion.
        # "breathe" - slow sine fade, speed set by idle_breathe_period_ms.
        # "pulse_once" - off most of the time, one breathing-style pulse
        #   whenever health status changes (green->yellow, etc.), then
        #   back to off - a transient "notice this changed" cue rather
        #   than a constant presence.
        # "off" - LED stays dark for plain health status entirely; still
        #   used normally for pairing/wifi-connecting/alert, which are
        #   all higher-priority than plain idle display.
        "idle_mode": "solid",
        "idle_breathe_period_ms": 4000,   # only used in "breathe" mode - deliberately slower than the 3000ms breathing used for boot/wifi-connecting (those signal "something's actively happening"; idle should read calmer)
    },
    "color_scheme": {
        # Health-tier (green/yellow/red) color choices - independent for
        # the web portal and the alert LED, since someone might want the
        # LED colorblind-safe but not care about the browser (already
        # readable via position/text, not just color) or vice versa.
        # "default" | "colorblind" | "custom". Colorblind swaps green
        # for blue and red for orange (the standard deuteranopia/
        # protanopia-safe pairing) - yellow is left alone, already
        # distinguishable from both. "custom" uses custom_colors below.
        "web_portal": "default",
        "alert_led": "default",
        # When True, changing either of the two fields above through the
        # settings page also updates the other to match, so they stay
        # equal - a convenience for "I just want one setting," not a
        # runtime behavior either surface needs to know about (each
        # still just reads its own field, which happens to already equal
        # the other's when synced - see web/server.py's settings save
        # handler for where the mirroring actually happens).
        "sync": False,
        "custom_colors": {
            "green": [0, 255, 0],
            "yellow": [255, 180, 0],
            "red": [255, 0, 0],
        },
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
        "high_intensity_hours_target": {"min": None, "max": None},
        # Optional, non-essential: some plants need a minimum amount of
        # direct/high-intensity light per day (min), and/or benefit from
        # a cap on it (max). Only becomes meaningful once thresholds.light_fc.red_max
        # is also set (that's what defines "high-intensity" in the first
        # place) - see core/light_tracker.py's evaluation logic.
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
        # Same placeholder reasoning as soil_calibration.min_delta_raw -
        # ~5% of the 0-65535 read_u16() range. Retune once the sensor is
        # characterized.
        "min_delta_raw": 3277,
    },
    "light_mode": {
        # "single": one continuous fc value via the 2-point dark/bright
        # calibration above (existing behavior).
        # "multi_point": instead of/alongside a continuous fc value, the
        # user samples named reference points (e.g. "Direct Sun",
        # "Bright Shade", "Low Light" - these are examples, not a fixed
        # list; the user names and adds their own). At runtime, the
        # dashboard shows a "Light Level" label for whichever configured
        # point's raw reading is closest to the current live reading -
        # nearest-neighbor classification, see
        # drivers/light_sensor.py's classify_light_level().
        #
        # Per-category daily duration tracking (e.g. "3h in direct sun
        # today") is NOT implemented here - deliberately out of scope
        # for the base device. Flagged as a future HACS integration
        # roadmap item instead (see README.md) - HA has far more room
        # for that kind of historical/statistical tracking than this
        # device does.
        "mode": "single",
        "points": [],  # list of {"label": str, "raw": int}, user-defined, any number
    },
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
