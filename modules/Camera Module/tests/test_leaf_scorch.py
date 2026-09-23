"""
test_leaf_scorch.py - stub-based tests for leaf_scorch.py against
synthetic "leaf on a background" images, demonstrating the one thing
that distinguishes it from necrosis.py: the SAME discoloration color
is detected when concentrated at the leaf's margin, but not when the
same total discolored area sits in the leaf's interior. Requires
numpy + opencv-python-headless.

Run: python3 test_leaf_scorch.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as config_module  # noqa: E402
from leaf_scorch import detect_leaf_scorch  # noqa: E402
from synthetic_images import HEALTHY_GREEN_BGR, NECROTIC_GREY_BROWN_BGR, leaf_on_background, paint_region  # noqa: E402

failures = []


def check(label, cond):
    status = "PASS" if cond else "FAIL"
    print("[{}] {}".format(status, label))
    if not cond:
        failures.append(label)


CFG = config_module.DEFAULT_CONFIG["detectors"]["leaf_scorch"]

# 100x100 canvas, leaf inset by 10px -> leaf occupies rows/cols 10-89 (an 80x80 region)
healthy_leaf = leaf_on_background(100, 100, HEALTHY_GREEN_BGR, margin=10)
result = detect_leaf_scorch(healthy_leaf, CFG)
check("a fully healthy leaf is not detected as scorched", result["detected"] is False)
check("result includes affected_margin_pct and confidence", "affected_margin_pct" in result and "confidence" in result)

# discoloration hugging the leaf's outer edge (a thin border band just inside the leaf boundary)
canvas_margin = leaf_on_background(100, 100, HEALTHY_GREEN_BGR, margin=10)
paint_region(canvas_margin, 10, 10, 90, 14, NECROTIC_GREY_BROWN_BGR)   # left edge strip
paint_region(canvas_margin, 10, 10, 14, 90, NECROTIC_GREY_BROWN_BGR)   # top edge strip
paint_region(canvas_margin, 10, 86, 90, 90, NECROTIC_GREY_BROWN_BGR)   # right edge strip
paint_region(canvas_margin, 86, 10, 90, 90, NECROTIC_GREY_BROWN_BGR)   # bottom edge strip
result_margin = detect_leaf_scorch(canvas_margin, CFG)
check("discoloration concentrated at the leaf's margin is detected as scorch", result_margin["detected"] is True)

# the exact same total discolored area, but placed in the leaf's interior (far from any edge) instead
canvas_interior = leaf_on_background(100, 100, HEALTHY_GREEN_BGR, margin=10)
paint_region(canvas_interior, 35, 35, 65, 65, NECROTIC_GREY_BROWN_BGR)  # 30x30, centered, nowhere near the boundary
result_interior = detect_leaf_scorch(canvas_interior, CFG)
check("the same discoloration in the leaf's INTERIOR is not detected as scorch (not margin-weighted the same way)", result_interior["detected"] is False)
check("margin-concentrated discoloration scores a higher affected_margin_pct than interior discoloration", result_margin["affected_margin_pct"] > result_interior["affected_margin_pct"])

print()
if failures:
    print("{} check(s) failed: {}".format(len(failures), failures))
    sys.exit(1)
else:
    print("All checks passed.")
