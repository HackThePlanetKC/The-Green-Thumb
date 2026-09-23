"""
test_necrosis.py - stub-based tests for necrosis.py against synthetic
BGR images. Requires numpy + opencv-python-headless.

Run: python3 test_necrosis.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as config_module  # noqa: E402
from necrosis import detect_necrosis  # noqa: E402
from synthetic_images import HEALTHY_GREEN_BGR, NECROTIC_GREY_BROWN_BGR, paint_region, solid_canvas  # noqa: E402

failures = []


def check(label, cond):
    status = "PASS" if cond else "FAIL"
    print("[{}] {}".format(status, label))
    if not cond:
        failures.append(label)


CFG = config_module.DEFAULT_CONFIG["detectors"]["necrosis"]

healthy = solid_canvas(40, 40, HEALTHY_GREEN_BGR)
result = detect_necrosis(healthy, CFG)
check("a fully healthy-green leaf is not detected as necrotic", result["detected"] is False)

canvas = solid_canvas(40, 40, HEALTHY_GREEN_BGR)
paint_region(canvas, 5, 5, 25, 25, NECROTIC_GREY_BROWN_BGR)  # 20x20 = 400px out of 1600px = 25%
result = detect_necrosis(canvas, CFG)
check("a grey/brown necrotic patch is detected", result["detected"] is True)
check("affected_area_pct roughly matches the painted fraction (~25%)", 15.0 <= result["affected_area_pct"] <= 35.0)

# NOTE: NECROTIC_BLACK_BGR itself (value ~10) is deliberately NOT used
# here - compute_leaf_mask() excludes near-black pixels as background
# (deep shadow, see detector_common.py's near_black_value_max=20), so
# a truly pure-black patch would be excluded from the leaf mask before
# necrosis ever sees it - a real, documented limitation (this pipeline
# can't currently distinguish very dark necrotic/black tissue from
# deep shadow). Using a dark-but-above-that-cutoff grey/black instead
# (value ~25) demonstrates the color range that DOES work.
canvas2 = solid_canvas(40, 40, HEALTHY_GREEN_BGR)
paint_region(canvas2, 5, 5, 25, 25, (25, 25, 25))
result2 = detect_necrosis(canvas2, CFG)
check("a dark grey/near-black necrotic patch (above the shadow-exclusion cutoff) is detected", result2["detected"] is True)

# necrosis is distributed uniformly across the WHOLE leaf, not margin-weighted -
# a patch in the interior (away from any edge) is just as detectable as one near an edge
canvas3 = solid_canvas(60, 60, HEALTHY_GREEN_BGR)
paint_region(canvas3, 24, 24, 36, 36, NECROTIC_GREY_BROWN_BGR)  # dead center, far from any edge
result3 = detect_necrosis(canvas3, CFG)
check("necrosis detects a patch in the leaf's interior, not just near edges", result3["detected"] is True)

# a tiny patch below the affected_threshold_pct: not detected
canvas4 = solid_canvas(100, 100, HEALTHY_GREEN_BGR)
paint_region(canvas4, 0, 0, 3, 3, NECROTIC_GREY_BROWN_BGR)  # 9px out of 10000px = 0.09%
result4 = detect_necrosis(canvas4, CFG)
check("a tiny necrotic patch below the threshold is not detected", result4["detected"] is False)

print()
if failures:
    print("{} check(s) failed: {}".format(len(failures), failures))
    sys.exit(1)
else:
    print("All checks passed.")
