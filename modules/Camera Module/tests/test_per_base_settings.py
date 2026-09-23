"""
test_per_base_settings.py - stub-based tests for
per_base_settings.PerBaseSettingsManager.

Run: python3 test_per_base_settings.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as config_module  # noqa: E402
from per_base_settings import PerBaseSettingsManager  # noqa: E402

failures = []


def check(label, cond):
    status = "PASS" if cond else "FAIL"
    print("[{}] {}".format(status, label))
    if not cond:
        failures.append(label)


class ConfigAtPath:
    def __init__(self, path):
        self._path = path

    def load(self):
        return config_module.load(self._path)

    def save(self, cfg):
        config_module.save(cfg, self._path)


with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, "config.json")
    bound = ConfigAtPath(path)
    mgr = PerBaseSettingsManager(bound)

    defaults = mgr.get_settings("A1B2C3")
    check("general_health defaults to True", defaults["general_health"] is True)
    check("every other metric defaults to False", all(defaults[m] is False for m in ("chlorosis", "necrosis", "spotting", "wilt_watch", "drama_level")))
    check("wilt_watch_config_necessary defaults to False", defaults["wilt_watch_config_necessary"] is False)
    check("max_saved_images defaults to 10", defaults["max_saved_images"] == 10)
    check("thumbnail_passthrough_enabled defaults to False", defaults["thumbnail_passthrough_enabled"] is False)

    mgr.set_metric("A1B2C3", "chlorosis", True)
    check("enabling chlorosis doesn't enable other metrics", mgr.get_settings("A1B2C3")["chlorosis"] is True and mgr.get_settings("A1B2C3")["necrosis"] is False)

    mgr.set_metric("A1B2C3", "wilt_watch", True)
    check("enabling wilt_watch for the first time sets config_necessary", mgr.get_settings("A1B2C3")["wilt_watch_config_necessary"] is True)

    mgr.clear_wilt_watch_config_necessary("A1B2C3")
    check("clear_wilt_watch_config_necessary() clears the flag", mgr.get_settings("A1B2C3")["wilt_watch_config_necessary"] is False)

    mgr.set_metric("A1B2C3", "wilt_watch", False)
    check("disabling wilt_watch doesn't set config_necessary", mgr.get_settings("A1B2C3")["wilt_watch_config_necessary"] is False)
    mgr.set_metric("A1B2C3", "wilt_watch", True)
    check("re-enabling wilt_watch with no has_reference check available (the default) re-sets config_necessary", mgr.get_settings("A1B2C3")["wilt_watch_config_necessary"] is True)

    # --- has_reference: when a caller CAN check (e.g. web_portal.py/mqtt_presence.py, both of which
    # have a WiltWatchManager handy), re-enabling wilt_watch for a base that already has a reference
    # image must NOT spuriously re-flag config_necessary ---
    mgr.clear_wilt_watch_config_necessary("A1B2C3")
    mgr.set_metric("A1B2C3", "wilt_watch", False)
    mgr.set_metric("A1B2C3", "wilt_watch", True, has_reference=lambda base_id: True)
    check("re-enabling wilt_watch with has_reference=True does NOT re-set config_necessary", mgr.get_settings("A1B2C3")["wilt_watch_config_necessary"] is False)

    mgr.set_metric("A1B2C3", "wilt_watch", False)
    mgr.set_metric("A1B2C3", "wilt_watch", True, has_reference=lambda base_id: False)
    check("re-enabling wilt_watch with has_reference=False still sets config_necessary", mgr.get_settings("A1B2C3")["wilt_watch_config_necessary"] is True)

    try:
        mgr.set_metric("A1B2C3", "general_health", False)
        raised = False
    except ValueError:
        raised = True
    check("general_health cannot be toggled - raises ValueError", raised)

    try:
        mgr.set_metric("A1B2C3", "not_a_real_metric", True)
        raised = False
    except ValueError:
        raised = True
    check("an unknown metric name raises ValueError", raised)

    mgr.set_metric("D4E5F6", "necrosis", True)
    check("settings are isolated per base_id", mgr.get_settings("A1B2C3")["necrosis"] is False and mgr.get_settings("D4E5F6")["necrosis"] is True)

    mgr2 = PerBaseSettingsManager(bound)
    check("a second manager instance reads the same persisted state", mgr2.get_settings("D4E5F6")["necrosis"] is True)

    # --- set_max_saved_images ---
    mgr.set_max_saved_images("A1B2C3", 25)
    check("set_max_saved_images() persists a new value", mgr.get_settings("A1B2C3")["max_saved_images"] == 25)
    check("set_max_saved_images() doesn't affect another base's own value", mgr.get_settings("D4E5F6")["max_saved_images"] == 10)

    for bad in (0, -1, 101, 3.5, "10"):
        try:
            mgr.set_max_saved_images("A1B2C3", bad)
            raised = False
        except ValueError:
            raised = True
        check("set_max_saved_images({!r}) out of range/type raises ValueError".format(bad), raised)
    check("a rejected set_max_saved_images() call leaves the previous value untouched", mgr.get_settings("A1B2C3")["max_saved_images"] == 25)

    try:
        mgr.set_max_saved_images("A1B2C3", True)  # bool is a subclass of int - must not be silently accepted as 1
        raised_bool = False
    except ValueError:
        raised_bool = True
    check("set_max_saved_images(True) is rejected - bool is not a valid integer cap here", raised_bool)

    # --- set_thumbnail_passthrough_enabled ---
    mgr.set_thumbnail_passthrough_enabled("A1B2C3", True)
    check("set_thumbnail_passthrough_enabled(True) persists", mgr.get_settings("A1B2C3")["thumbnail_passthrough_enabled"] is True)
    check("thumbnail toggle doesn't affect another base", mgr.get_settings("D4E5F6")["thumbnail_passthrough_enabled"] is False)
    mgr.set_thumbnail_passthrough_enabled("A1B2C3", False)
    check("set_thumbnail_passthrough_enabled(False) persists", mgr.get_settings("A1B2C3")["thumbnail_passthrough_enabled"] is False)

    # --- a base_id whose entry was created before these two settings existed still gets sane defaults filled in ---
    old_style_path = os.path.join(d, "old_style_config.json")
    old_style_bound = ConfigAtPath(old_style_path)
    old_cfg = old_style_bound.load()
    old_cfg["per_base_settings"]["LEGACY1"] = {"chlorosis": True}  # simulates an entry saved before max_saved_images/thumbnail existed
    old_style_bound.save(old_cfg)
    legacy_mgr = PerBaseSettingsManager(old_style_bound)
    legacy_settings = legacy_mgr.get_settings("LEGACY1")
    check("a pre-existing base entry missing the new keys still reports sane defaults", legacy_settings["max_saved_images"] == 10 and legacy_settings["thumbnail_passthrough_enabled"] is False)
    check("a pre-existing base entry's other (real) settings are preserved", legacy_settings["chlorosis"] is True)
    legacy_mgr.set_max_saved_images("LEGACY1", 3)
    check("set_max_saved_images() on a pre-existing entry backfills the other missing default first", legacy_mgr.get_settings("LEGACY1")["thumbnail_passthrough_enabled"] is False)

print()
if failures:
    print("{} check(s) failed: {}".format(len(failures), failures))
    sys.exit(1)
else:
    print("All checks passed.")
