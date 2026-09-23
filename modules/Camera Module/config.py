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
        # Module-global on/off for the whole flash subsystem (added
        # alongside grid/settings work) - lets a user disable the ring
        # entirely (e.g. if it's causing glare, see BUILD.md) without
        # losing the tuned threshold/brightness values underneath.
        "enabled": True,
    },
    "wifi": {
        # Same shape as the base station's own core/config.py wifi
        # dict, for consistency - and stored the same way it is there:
        # plain JSON, no encryption. That's not a new gap introduced
        # here, it's the existing project-wide approach (see
        # wifi_manager.py's module docstring) - the base station's own
        # WiFi password sits in its config.json the same way.
        "ssid": "",
        "password": "",
    },
    "mqtt": {
        # Broker address/port are user-entered - no auto-discovery
        # mechanism exists anywhere in this project (the base
        # station's own settings page requires manual broker entry
        # too, see web/server.py's _handle_settings_mqtt in the base
        # firmware repo). Bundled into this module's WiFi setup step
        # specifically because base discovery (this module's very next
        # setup step, see mqtt_discovery.py) needs a broker connection
        # to work at all - unlike the base station, which can defer
        # broker configuration to a settings page it reaches over a
        # network connection it already has by then.
        "broker": "",
        "port": 1883,
    },
    # List of base_id strings (never friendly_name - see
    # base_association.py for why) this camera module is associated
    # with. Empty until the user completes the association step.
    "associated_base_ids": [],
    "capture": {
        # How often a scheduled capture runs, per day. Capture
        # scheduling itself isn't built yet (see README.md
        # "Remaining") - this is the setting a future scheduler will
        # read, exposed now so it has one home in config/MQTT/HA from
        # the start rather than being bolted on later.
        "frequency_per_day": 2,
    },
    # Grid layout: how the camera's frame is divided into regions, and
    # which associated base each region belongs to. Module-global (see
    # grid_config.py) - editable only via the camera portal or HA, not
    # any individual base's portal (see docs/ARCHITECTURE.md).
    "grid": {
        "rows": 1,
        "cols": 1,
        # "row,col" (0-indexed) -> base_id. A cell absent from this
        # dict is unassigned. Deliberately a flat dict keyed by a
        # string cell coordinate, not a 2D list - JSON object keys
        # must be strings anyway, and a flat dict means an unassigned
        # cell simply has no entry rather than needing an explicit
        # null placeholder in a full rows*cols grid.
        "cells": {},
    },
    # Per-base opt-in metric settings, keyed by base_id (a flat dict
    # under this one key, not separate top-level keys per base - keeps
    # _deep_merge_defaults simple, and there's no fixed set of base_ids
    # to enumerate in DEFAULT_CONFIG ahead of time). general_health is
    # NOT stored here - it's always on and not user-toggleable, see
    # per_base_settings.py. Each entry, once a base has any setting
    # touched, has the shape:
    #   {"chlorosis": bool, "necrosis": bool, "spotting": bool,
    #    "leaf_scorch": bool, "powdery_mildew": bool,
    #    "pest_indicators": bool, "wilt_watch": bool,
    #    "drama_level": bool, "wilt_watch_config_necessary": bool,
    #    "max_saved_images": int, "thumbnail_passthrough_enabled": bool}
    # The last two are per-zone image-library settings (see
    # image_library.py) - unlike the boolean detector/comparison
    # toggles above, max_saved_images is a numeric cap, but it lives in
    # this same per-base_id dict rather than a separate top-level key,
    # for the same reasons: three-way parity (camera portal/that base's
    # own portal/HA), and a base can only ever edit its own entry.
    "per_base_settings": {},
    # Image library (see image_library.py): local per-zone photo
    # storage/retention, independent of wilt_watch.py/drama_level.py's
    # own separate grayscale-only comparison storage (different
    # consumers - viewing/downloading a real photo vs. structural
    # comparison math - deliberately NOT unified into one file per the
    # decision recorded in decisions-and-practices.md).
    "image_library": {
        # Longest-side pixel dimension for the downsampled thumbnail
        # optionally passed through to HA over MQTT (item 10) - module-
        # global, not per-zone (the per-zone setting is only whether
        # passthrough is ON, see per_base_settings.py's
        # thumbnail_passthrough_enabled) - same "thresholds are
        # module-global" convention as detectors.* above, see
        # decisions-and-practices.md. Small default on purpose - a
        # thumbnail for an HA dashboard tile, not a full-res image
        # (that's what the HTTP download endpoint is for).
        "thumbnail_max_dimension": 320,
    },
    # Thresholds/sensitivity for the six heuristic visual detectors
    # (chlorosis.py, necrosis.py, spotting.py, leaf_scorch.py,
    # powdery_mildew.py, pest_indicators.py). Module-global, not
    # per-base, even though each detector's ENABLED toggle is per-base
    # (per_base_settings.py) - a deliberate interpretation where the
    # task's own instruction ("consistent with existing threshold
    # config patterns in the project") pointed one way, since every
    # existing threshold in this project (flash.low_light_threshold,
    # the base station's own sensor thresholds) is module/device-
    # global, not per-base. See decisions-and-practices.md. Sane
    # starting defaults, not sourced from a dataset - same "adjustable,
    # not fixed, tune once pointed at real plants" spirit as this
    # module's other thresholds.
    "detectors": {
        "chlorosis": {
            "green_hue_min": 35,        # OpenCV hue 0-179; healthy-green reference band
            "green_hue_max": 85,
            "std_dev_multiplier": 1.5,  # how far below the reference green's mean hue counts as "shifted"
            "yellow_hue_floor": 15,     # excludes red/brown/orange - keeps this detector out of necrosis/scorch territory
            "saturation_min": 60,       # excludes washed-out/desaturated pixels
            "affected_threshold_pct": 5.0,
        },
        "necrosis": {
            "saturation_max": 60,
            "value_max": 90,
            "affected_threshold_pct": 3.0,
        },
        "spotting": {
            "color_distance_threshold": 40,  # LAB Euclidean distance from the leaf's own mean color
            "min_lesion_area_px": 30,        # deliberately larger than pest_clusters/stippling's minimums - "few larger lesions"
            "min_lesion_count": 1,
            "affected_threshold_pct": 2.0,
        },
        "leaf_scorch": {
            "saturation_max": 60,
            "value_max": 100,
            "margin_band_fraction": 0.15,  # outer 15% (by distance-from-edge, not area) of the leaf
            "affected_threshold_pct": 10.0,
        },
        "powdery_mildew": {
            "saturation_max": 40,
            "value_min": 180,
            "texture_variance_min": 15.0,  # per-pixel Laplacian magnitude
            "affected_threshold_pct": 5.0,
        },
        "pest_indicators": {
            "webbing": {
                "canny_low": 50,
                "canny_high": 150,
                "edge_density_min": 0.03,
            },
            "pest_clusters": {
                "color_distance_threshold": 40,
                "max_component_area_px": 25,  # deliberately smaller than spotting.py's minimum - "many small objects"
                "min_component_count": 8,
            },
            "stippling": {
                "saturation_max": 50,
                "value_min": 170,
                "max_component_area_px": 10,
                "min_component_count": 15,
            },
        },
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


def set_by_path(config, path, value):
    """
    Writes a nested config value using dot notation - same semantics as
    the base station's own core/config.py set_by_path(): raises
    KeyError if the path (including the final key) doesn't already
    exist, so a malformed MQTT command can't inject arbitrary config
    structure. Does not save to disk - caller calls save() after.

    Used by mqtt_presence.py's global set_config command handler,
    which additionally restricts which top-level keys it will forward
    here at all (see that file) - this function itself has no
    awareness of "global vs per-base vs credentials", it just writes
    to an existing path.
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
