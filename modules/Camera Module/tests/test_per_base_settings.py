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

    mgr.set_metric("A1B2C3", "chlorosis", True)
    check("enabling chlorosis doesn't enable other metrics", mgr.get_settings("A1B2C3")["chlorosis"] is True and mgr.get_settings("A1B2C3")["necrosis"] is False)

    mgr.set_metric("A1B2C3", "wilt_watch", True)
    check("enabling wilt_watch for the first time sets config_necessary", mgr.get_settings("A1B2C3")["wilt_watch_config_necessary"] is True)

    mgr.clear_wilt_watch_config_necessary("A1B2C3")
    check("clear_wilt_watch_config_necessary() clears the flag", mgr.get_settings("A1B2C3")["wilt_watch_config_necessary"] is False)

    mgr.set_metric("A1B2C3", "wilt_watch", False)
    check("disabling wilt_watch doesn't set config_necessary", mgr.get_settings("A1B2C3")["wilt_watch_config_necessary"] is False)
    mgr.set_metric("A1B2C3", "wilt_watch", True)
    check("re-enabling wilt_watch (after having been disabled) sets config_necessary again", mgr.get_settings("A1B2C3")["wilt_watch_config_necessary"] is True)

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

print()
if failures:
    print("{} check(s) failed: {}".format(len(failures), failures))
    sys.exit(1)
else:
    print("All checks passed.")
