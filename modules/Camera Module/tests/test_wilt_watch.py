"""
test_wilt_watch.py - stub-based tests for wilt_watch.WiltWatchManager,
against a throwaway data_dir and a real PerBaseSettingsManager bound to
a throwaway config file - no real camera or reference image ever
touched, everything is synthetic GrayscaleImage data.

Run: python3 test_wilt_watch.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as config_module  # noqa: E402
from image_compare import GrayscaleImage  # noqa: E402
from per_base_settings import PerBaseSettingsManager  # noqa: E402
from wilt_watch import WiltWatchManager  # noqa: E402

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


def flat_image(width, height, value):
    return GrayscaleImage(width, height, [value] * (width * height))


with tempfile.TemporaryDirectory() as d:
    config_path = os.path.join(d, "config.json")
    data_dir = os.path.join(d, "data")
    per_base = PerBaseSettingsManager(ConfigAtPath(config_path))
    per_base.set_metric("A1B2C3", "wilt_watch", True)
    check("enabling wilt_watch before any reference exists sets config_necessary", per_base.get_settings("A1B2C3")["wilt_watch_config_necessary"] is True)

    watch = WiltWatchManager(per_base, data_dir=data_dir)
    check("has_reference() is False before any capture_reference() call", watch.has_reference("A1B2C3") is False)
    check("compute_wilt_level() returns None with no reference yet - never computes a level (item 9)", watch.compute_wilt_level("A1B2C3", flat_image(10, 10, 100)) is None)

    reference = flat_image(10, 10, 200)
    watch.capture_reference("A1B2C3", reference)
    check("capture_reference() makes has_reference() True", watch.has_reference("A1B2C3") is True)
    check("capture_reference() clears wilt_watch_config_necessary", per_base.get_settings("A1B2C3")["wilt_watch_config_necessary"] is False)

    same_as_reference = flat_image(10, 10, 200)
    check("comparing the reference against an identical capture gives zero wilt level", watch.compute_wilt_level("A1B2C3", same_as_reference) == 0.0)

    different_capture = flat_image(10, 10, 0)  # fully dark - "first dark row" profile shifts to row 0
    level = watch.compute_wilt_level("A1B2C3", different_capture)
    check("a structurally different capture gives a nonzero wilt level", level is not None and level > 0.0)

    # a second base is unaffected by the first base's reference/flag state
    check("wilt_watch_config_necessary is per-base, not global", per_base.get_settings("D4E5F6")["wilt_watch_config_necessary"] is False)
    check("has_reference() is per-base", watch.has_reference("D4E5F6") is False)

    # capturing a new reference overwrites the old one
    watch.capture_reference("A1B2C3", flat_image(10, 10, 50))
    check("capturing a new reference overwrites the previous one", watch.compute_wilt_level("A1B2C3", flat_image(10, 10, 50)) == 0.0)

print()
if failures:
    print("{} check(s) failed: {}".format(len(failures), failures))
    sys.exit(1)
else:
    print("All checks passed.")
