"""
test_detector_common.py - stub-based tests for detector_common.py's
shared utilities: compute_leaf_mask(), confidence_from_ratio(),
color_outlier_mask(), leaf_area(). Requires numpy + opencv-python-
headless installed (see detector_common.py's own module docstring for
why these detector tests genuinely need the real library, unlike most
of this module's other tests).

Run: python3 test_detector_common.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from detector_common import color_outlier_mask, compute_leaf_mask, confidence_from_ratio, leaf_area  # noqa: E402
from synthetic_images import (  # noqa: E402
    HEALTHY_GREEN_BGR, RED_OUTLIER_BGR, leaf_on_background, paint_region, solid_canvas,
)

failures = []


def check(label, cond):
    status = "PASS" if cond else "FAIL"
    print("[{}] {}".format(status, label))
    if not cond:
        failures.append(label)


# --- compute_leaf_mask ---
img = leaf_on_background(40, 40, HEALTHY_GREEN_BGR, margin=10)
mask = compute_leaf_mask(img)
check("compute_leaf_mask() flags the near-white background as NOT leaf", mask[0, 0] == 0)
check("compute_leaf_mask() flags the leaf interior as leaf", mask[20, 20] == 255)
check("leaf_area() counts only the leaf pixels, less than the full canvas", 0 < leaf_area(mask) < 40 * 40)

near_black = solid_canvas(10, 10, (5, 5, 5))
check("compute_leaf_mask() excludes near-black (shadow) as background", int(compute_leaf_mask(near_black)[5, 5]) == 0)

all_green = solid_canvas(10, 10, HEALTHY_GREEN_BGR)
check("compute_leaf_mask() treats a full-frame healthy-green canvas as entirely leaf", leaf_area(compute_leaf_mask(all_green)) == 100)

# --- confidence_from_ratio ---
check("confidence_from_ratio(0, threshold) is 0.0", confidence_from_ratio(0, 10) == 0.0)
check("confidence_from_ratio(threshold, threshold) is 0.5 (borderline)", confidence_from_ratio(10, 10) == 0.5)
check("confidence_from_ratio at 2x threshold (default saturate_multiplier) reaches the cap", confidence_from_ratio(20, 10) == 1.0)
check("confidence_from_ratio never exceeds the cap even at very high values", confidence_from_ratio(1000, 10) == 1.0)
check("confidence_from_ratio respects a custom cap", confidence_from_ratio(1000, 10, cap=0.7) == 0.7)
check("confidence_from_ratio with threshold<=0 and value>0 returns the cap", confidence_from_ratio(5, 0) == 1.0)
check("confidence_from_ratio with threshold<=0 and value==0 returns 0.0", confidence_from_ratio(0, 0) == 0.0)

# --- color_outlier_mask ---
canvas = solid_canvas(20, 20, HEALTHY_GREEN_BGR)
paint_region(canvas, 5, 5, 10, 10, RED_OUTLIER_BGR)
leaf_mask = compute_leaf_mask(canvas)
outliers = color_outlier_mask(canvas, leaf_mask, distance_threshold=40)
check("color_outlier_mask() flags the color-distinct red patch", outliers[7, 7] == 255)
check("color_outlier_mask() does not flag the uniform green background", outliers[0, 0] == 0)

print()
if failures:
    print("{} check(s) failed: {}".format(len(failures), failures))
    sys.exit(1)
else:
    print("All checks passed.")
