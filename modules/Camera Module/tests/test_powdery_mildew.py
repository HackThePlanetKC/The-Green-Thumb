"""
test_powdery_mildew.py - stub-based tests for powdery_mildew.py,
demonstrating that BOTH color and texture are required together (per
item 5): a flat white patch (color, no texture) and a colored-but-
textured patch (texture, wrong color) should each fail to trigger,
while a light-colored, textured patch triggers. Requires numpy +
opencv-python-headless.

Run: python3 test_powdery_mildew.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as config_module  # noqa: E402
from powdery_mildew import detect_powdery_mildew  # noqa: E402
from synthetic_images import HEALTHY_GREEN_BGR, paint_checker_texture, paint_region, solid_canvas  # noqa: E402

failures = []


def check(label, cond):
    status = "PASS" if cond else "FAIL"
    print("[{}] {}".format(status, label))
    if not cond:
        failures.append(label)


CFG = config_module.DEFAULT_CONFIG["detectors"]["powdery_mildew"]

healthy = solid_canvas(60, 60, HEALTHY_GREEN_BGR)
result = detect_powdery_mildew(healthy, CFG)
check("a fully healthy leaf is not detected as mildewed", result["detected"] is False)

# color + texture together: a light-grey/off-white "dusty coating" region with real
# per-pixel high-frequency variation. NOTE: deliberately NOT pure/blown-out white
# (e.g. 245,245,245) - compute_leaf_mask() itself excludes near-white pixels as
# presumed background (a pot, wall, paper - see that function's own docstring),
# so a mildew coating bright enough to cross that same near-white cutoff would be
# masked out before this detector ever sees it. A real, documented edge case
# (see compute_leaf_mask()'s docstring) - not exercised by this happy-path test,
# which uses realistic "off-white dusty" values comfortably below that cutoff.
canvas_coating = solid_canvas(60, 60, HEALTHY_GREEN_BGR)
paint_checker_texture(canvas_coating, 10, 10, 50, 50, (195, 195, 195), (165, 165, 165), cell=2)
result_coating = detect_powdery_mildew(canvas_coating, CFG)
check("a light-colored, texturally-varied coating is detected as mildew", result_coating["detected"] is True)

# color WITHOUT texture: a flat, solid off-white patch (same color range as the
# coating test above, but no internal variation) should not trigger - isolates
# "texture is required too" from the leaf-mask edge case noted above (this uses
# the same in-leaf-mask value range, not pure white, so the negative result here
# is genuinely about the missing texture signal, not about being masked out).
canvas_flat = solid_canvas(60, 60, HEALTHY_GREEN_BGR)
paint_region(canvas_flat, 10, 10, 50, 50, (195, 195, 195))
result_flat = detect_powdery_mildew(canvas_flat, CFG)
check("a flat, untextured off-white patch (color only) is NOT detected as mildew - texture is required too", result_flat["detected"] is False)

# texture WITHOUT the right color: a green/dark checkerboard (real high-frequency variation, wrong color)
canvas_wrong_color = solid_canvas(60, 60, HEALTHY_GREEN_BGR)
paint_checker_texture(canvas_wrong_color, 10, 10, 50, 50, (30, 100, 30), (50, 180, 50), cell=2)
result_wrong_color = detect_powdery_mildew(canvas_wrong_color, CFG)
check("a textured but non-light-colored region is NOT detected as mildew - color is required too", result_wrong_color["detected"] is False)

print()
if failures:
    print("{} check(s) failed: {}".format(len(failures), failures))
    sys.exit(1)
else:
    print("All checks passed.")
