"""
necrosis.py - Heuristic necrosis (dead/dying tissue) detection.

Low-saturation dark/brown/grey/black patch detection within the leaf
mask, per this task's item 2 - measured uniformly across the whole
leaf area, deliberately NOT weighted by position. That's the one thing
distinguishing this from leaf_scorch.py, which reuses the exact same
color primitive (discoloration.detect_dark_patches) but weights it by
distance from the leaf's margin - necrosis can appear anywhere on a
leaf (disease, physical damage, rot), while scorch is characteristically
edge/tip-concentrated (heat/drought stress moving inward from the
margin). See that file for the margin-weighted variant.
"""

from detector_common import compute_leaf_mask, confidence_from_ratio, leaf_area, require_cv2, np
from discoloration import detect_dark_patches


def detect_necrosis(bgr_image, cfg):
    """
    cfg keys (see config.py's DEFAULT_CONFIG["detectors"]["necrosis"]):
    saturation_max/value_max - passed straight through to
        discoloration.detect_dark_patches().
    affected_threshold_pct - % of leaf area needed before `detected`
        flips True.
    """
    require_cv2()
    leaf_mask = compute_leaf_mask(bgr_image)
    total_leaf = leaf_area(leaf_mask)
    if total_leaf == 0:
        return {"detected": False, "affected_area_pct": 0.0, "confidence": 0.0}

    patch_mask = detect_dark_patches(bgr_image, leaf_mask, cfg["saturation_max"], cfg["value_max"])
    affected = int(np.count_nonzero(patch_mask))
    pct = 100.0 * affected / total_leaf
    detected = bool(pct >= cfg["affected_threshold_pct"])
    return {
        "detected": detected,
        "affected_area_pct": float(pct),
        "confidence": float(confidence_from_ratio(pct, cfg["affected_threshold_pct"])),
    }
