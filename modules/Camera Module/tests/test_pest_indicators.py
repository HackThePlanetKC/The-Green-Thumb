"""
test_pest_indicators.py - stub-based tests for pest_indicators.py's
three sub-checks (webbing, pest_clusters, stippling), plus a cross-
file check against powdery_mildew.py demonstrating the distinguishing
behavior documented in pest_indicators.py's own module docstring (item
6c): a dense field of tiny discrete light dots should register far
more strongly as stippling (many small components) than as powdery
mildew (which wants a texturally-varied, largely contiguous coated
area), even though both are "light speckling" and can visually
overlap. Requires numpy + opencv-python-headless.

Run: python3 test_pest_indicators.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402

import config as config_module  # noqa: E402
from pest_indicators import _CONFIDENCE_CAP, detect_pest_indicators  # noqa: E402
from powdery_mildew import detect_powdery_mildew  # noqa: E402
from synthetic_images import (  # noqa: E402
    HEALTHY_GREEN_BGR, NECROTIC_GREY_BROWN_BGR, draw_web_lines, scatter_dots, solid_canvas,
)

failures = []


def check(label, cond):
    status = "PASS" if cond else "FAIL"
    print("[{}] {}".format(status, label))
    if not cond:
        failures.append(label)


CFG = config_module.DEFAULT_CONFIG["detectors"]["pest_indicators"]
MILDEW_CFG = config_module.DEFAULT_CONFIG["detectors"]["powdery_mildew"]

# --- a fully healthy, unblemished leaf: nothing triggers ---
healthy = solid_canvas(100, 100, HEALTHY_GREEN_BGR)
result = detect_pest_indicators(healthy, CFG)
check("a fully healthy leaf triggers no pest sub-checks", result["triggered"] == [])
check("a fully healthy leaf is not detected overall", result["detected"] is False)
check("every sub-check reports a confidence score", all("confidence" in result[k] for k in ("webbing", "pest_clusters", "stippling")))

# --- webbing: many thin, high-contrast thread lines ---
canvas_web = solid_canvas(100, 100, HEALTHY_GREEN_BGR)
rng = np.random.default_rng(1)
draw_web_lines(canvas_web, count=60, region=(10, 10, 90, 90), rng=rng)
result_web = detect_pest_indicators(canvas_web, CFG)
check("dense thread-like lines trigger the webbing sub-check", result_web["webbing"]["detected"] is True)
check("webbing triggering is reflected in the grouped `triggered` list", "webbing" in result_web["triggered"])
check("webbing sub-check confidence is capped below other detectors' 1.0 ceiling", result_web["webbing"]["confidence"] <= _CONFIDENCE_CAP)

# --- pest_clusters: many small, clustered color-outlier blobs ---
canvas_clusters = solid_canvas(100, 100, HEALTHY_GREEN_BGR)
rng = np.random.default_rng(2)
scatter_dots(canvas_clusters, count=20, dot_radius=1, bgr=NECROTIC_GREY_BROWN_BGR, region=(20, 20, 80, 80), rng=rng, min_gap=3)
result_clusters = detect_pest_indicators(canvas_clusters, CFG)
check("many small clustered dark blobs trigger the pest_clusters sub-check", result_clusters["pest_clusters"]["detected"] is True)
check("pest_clusters triggering is reflected in the grouped `triggered` list", "pest_clusters" in result_clusters["triggered"])

# --- spotting-style few larger lesions do NOT trigger pest_clusters (opposite tuning) ---
canvas_few_large = solid_canvas(100, 100, HEALTHY_GREEN_BGR)
canvas_few_large[20:35, 20:35] = NECROTIC_GREY_BROWN_BGR
canvas_few_large[60:75, 60:75] = NECROTIC_GREY_BROWN_BGR
result_few_large = detect_pest_indicators(canvas_few_large, CFG)
check("a couple of large lesions (spotting's territory) do NOT trigger pest_clusters (too few, too large)", result_few_large["pest_clusters"]["detected"] is False)

# --- stippling: many small, discrete LIGHT specks ---
# NOTE: (195,195,195), not a brighter/purer white - same compute_leaf_mask()
# near-white background-exclusion edge case as powdery_mildew.py's own tests
# (see test_powdery_mildew.py and compute_leaf_mask()'s docstring); a color
# above that cutoff would be masked out as background before this detector
# ever saw it, regardless of how many dots were drawn.
canvas_stippling = solid_canvas(120, 120, HEALTHY_GREEN_BGR)
rng = np.random.default_rng(3)
scatter_dots(canvas_stippling, count=40, dot_radius=1, bgr=(195, 195, 195), region=(10, 10, 110, 110), rng=rng, min_gap=3)
result_stippling = detect_pest_indicators(canvas_stippling, CFG)
check("many small discrete light dots trigger the stippling sub-check", result_stippling["stippling"]["detected"] is True)
check("stippling triggering is reflected in the grouped `triggered` list", "stippling" in result_stippling["triggered"])

# --- distinguishing stippling from powdery mildew (item 6c) ---
# The SAME light-dot pattern registers strongly as stippling (many small
# components) but should register much more weakly - and stay below its
# own detection threshold - as powdery mildew, which wants a largely
# contiguous, texturally-varied coated area rather than scattered dots.
mildew_result_on_stippling_image = detect_powdery_mildew(canvas_stippling, MILDEW_CFG)
check(
    "a stippling-pattern image scores low on powdery_mildew's affected_area_pct (dots, not a coating)",
    mildew_result_on_stippling_image["affected_area_pct"] < MILDEW_CFG["affected_threshold_pct"],
)
check("the same stippling-pattern image is NOT detected as powdery mildew", mildew_result_on_stippling_image["detected"] is False)

# ... and the reverse: a genuine mildew coating (contiguous, textured) does not
# register as stippling, since its light pixels mostly merge into one large
# connected component well above stippling's max_component_area_px.
from powdery_mildew import detect_powdery_mildew as _dpm  # noqa: E402
from synthetic_images import paint_checker_texture  # noqa: E402

canvas_coating = solid_canvas(100, 100, HEALTHY_GREEN_BGR)
paint_checker_texture(canvas_coating, 10, 10, 90, 90, (195, 195, 195), (165, 165, 165), cell=2)
coating_pest_result = detect_pest_indicators(canvas_coating, CFG)
check("a genuine contiguous mildew coating does NOT trigger the stippling sub-check", coating_pest_result["stippling"]["detected"] is False)
check("that same coating IS correctly detected as powdery mildew", _dpm(canvas_coating, MILDEW_CFG)["detected"] is True)

# --- total_leaf == 0 (no leaf detected at all, e.g. lens cap/blown-out frame): each sub-check
# gets its OWN dict (not one shared/aliased object across all three), matching the normal
# (non-empty) result shape so a consumer reading e.g. result["pest_clusters"]["component_count"]
# doesn't KeyError just because this particular capture had no leaf area ---
from synthetic_images import BACKGROUND_BGR  # noqa: E402

no_leaf_canvas = solid_canvas(50, 50, BACKGROUND_BGR)
empty_result = detect_pest_indicators(no_leaf_canvas, CFG)
check("no-leaf-detected frame is not detected overall", empty_result["detected"] is False)
check("no-leaf-detected frame's webbing sub-result has the same shape as a normal one (edge_density key)", "edge_density" in empty_result["webbing"])
check("no-leaf-detected frame's pest_clusters sub-result has the same shape as a normal one (component_count key)", "component_count" in empty_result["pest_clusters"])
check("no-leaf-detected frame's stippling sub-result has the same shape as a normal one (component_count key)", "component_count" in empty_result["stippling"])
check("the three empty sub-results are NOT the same aliased dict object", empty_result["webbing"] is not empty_result["pest_clusters"] and empty_result["pest_clusters"] is not empty_result["stippling"])

# mutating one sub-result must not affect the others (would fail if they were aliased)
empty_result["webbing"]["detected"] = True
check("mutating one sub-result's dict doesn't leak into the others (not aliased)", empty_result["pest_clusters"]["detected"] is False and empty_result["stippling"]["detected"] is False)

print()
if failures:
    print("{} check(s) failed: {}".format(len(failures), failures))
    sys.exit(1)
else:
    print("All checks passed.")
