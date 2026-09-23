"""
test_chlorosis.py - stub-based tests for chlorosis.py against
synthetic BGR images (a healthy-green canvas with a painted-in yellow
patch) - no real photo required. Requires numpy + opencv-python-
headless (see detector_common.py's module docstring).

Run: python3 test_chlorosis.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as config_module  # noqa: E402
from chlorosis import detect_chlorosis  # noqa: E402
from synthetic_images import HEALTHY_GREEN_BGR, YELLOW_CHLOROTIC_BGR, paint_region, solid_canvas  # noqa: E402

failures = []


def check(label, cond):
    status = "PASS" if cond else "FAIL"
    print("[{}] {}".format(status, label))
    if not cond:
        failures.append(label)


CFG = config_module.DEFAULT_CONFIG["detectors"]["chlorosis"]

# --- a fully healthy-green leaf: no chlorosis ---
healthy = solid_canvas(40, 40, HEALTHY_GREEN_BGR)
result = detect_chlorosis(healthy, CFG)
check("a fully healthy-green leaf is not detected as chlorotic", result["detected"] is False)
check("a fully healthy-green leaf has ~0% affected area", result["affected_area_pct"] < 1.0)
check("result includes a confidence score", "confidence" in result and 0.0 <= result["confidence"] <= 1.0)

# --- a leaf with a clear yellow patch: chlorosis detected, roughly matching the painted fraction ---
canvas = solid_canvas(40, 40, HEALTHY_GREEN_BGR)
paint_region(canvas, 10, 10, 30, 30, YELLOW_CHLOROTIC_BGR)  # 20x20 = 400px out of 1600px = 25%
result = detect_chlorosis(canvas, CFG)
check("a leaf with a yellow patch is detected as chlorotic", result["detected"] is True)
check("affected_area_pct roughly matches the painted yellow fraction (~25%)", 15.0 <= result["affected_area_pct"] <= 35.0)
check("confidence is higher for a clear, well-above-threshold case", result["confidence"] > 0.5)

# --- a leaf with only a small yellow patch, below the affected_threshold_pct: not detected ---
canvas_small = solid_canvas(100, 100, HEALTHY_GREEN_BGR)
paint_region(canvas_small, 0, 0, 5, 5, YELLOW_CHLOROTIC_BGR)  # 25px out of 10000px = 0.25%
result = detect_chlorosis(canvas_small, CFG)
check("a tiny yellow patch below the threshold is not detected", result["detected"] is False)

# --- no green reference population in frame at all: can't establish 'expected variance', so nothing is flagged ---
no_green = solid_canvas(20, 20, YELLOW_CHLOROTIC_BGR)
result = detect_chlorosis(no_green, CFG)
check("with no healthy-green reference pixels in frame, chlorosis is not (over-)detected", result["detected"] is False)

print()
if failures:
    print("{} check(s) failed: {}".format(len(failures), failures))
    sys.exit(1)
else:
    print("All checks passed.")
