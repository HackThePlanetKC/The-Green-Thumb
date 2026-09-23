"""
test_spotting.py - stub-based tests for spotting.py against synthetic
BGR images with a controlled number of painted "lesions". Requires
numpy + opencv-python-headless.

Run: python3 test_spotting.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as config_module  # noqa: E402
from spotting import detect_spotting  # noqa: E402
from synthetic_images import HEALTHY_GREEN_BGR, RED_OUTLIER_BGR, paint_region, scatter_dots, solid_canvas  # noqa: E402

failures = []


def check(label, cond):
    status = "PASS" if cond else "FAIL"
    print("[{}] {}".format(status, label))
    if not cond:
        failures.append(label)


CFG = config_module.DEFAULT_CONFIG["detectors"]["spotting"]

healthy = solid_canvas(60, 60, HEALTHY_GREEN_BGR)
result = detect_spotting(healthy, CFG)
check("a fully healthy leaf has zero lesions", result["lesion_count"] == 0)
check("a fully healthy leaf is not detected", result["detected"] is False)

# a few larger, well-separated lesions - this is spotting's own tuning (few larger, not many small)
canvas = solid_canvas(80, 80, HEALTHY_GREEN_BGR)
paint_region(canvas, 5, 5, 13, 13, RED_OUTLIER_BGR)     # 8x8 = 64px, above min_lesion_area_px (30)
paint_region(canvas, 40, 40, 48, 48, RED_OUTLIER_BGR)
paint_region(canvas, 60, 10, 68, 18, RED_OUTLIER_BGR)
result = detect_spotting(canvas, CFG)
check("three separated, adequately-sized lesions are all counted", result["lesion_count"] == 3)
check("three lesions triggers detected=True", result["detected"] is True)
check("result includes affected_area_pct", result["affected_area_pct"] > 0.0)

# tiny noise specks below min_lesion_area_px don't count as real lesions
canvas_noise = solid_canvas(80, 80, HEALTHY_GREEN_BGR)
rng = __import__("numpy").random.default_rng(42)
scatter_dots(canvas_noise, count=10, dot_radius=1, bgr=RED_OUTLIER_BGR, region=(0, 0, 80, 80), rng=rng, min_gap=5)
result = detect_spotting(canvas_noise, CFG)
check("many tiny (below min_lesion_area_px) specks are filtered out, not counted as lesions", result["lesion_count"] == 0)
check("tiny noise specks don't trigger detected", result["detected"] is False)

print()
if failures:
    print("{} check(s) failed: {}".format(len(failures), failures))
    sys.exit(1)
else:
    print("All checks passed.")
