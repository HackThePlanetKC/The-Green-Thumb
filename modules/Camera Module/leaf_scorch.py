"""
leaf_scorch.py - Heuristic leaf scorch detection.

Per this task's item 4: reuses necrosis.py's brown/tan discoloration
primitive (discoloration.detect_dark_patches - the exact same color
logic, see that file), but weights it by distance from the leaf's
margin/tip instead of measuring uniformly across the whole leaf like
necrosis.py does. Scorch (heat/drought/salt stress) characteristically
starts at leaf edges and tips and moves inward, unlike necrosis's
random/whole-leaf distribution - that's the one thing distinguishing
these two detectors; everything else about the underlying color
detection is identical and lives in discoloration.py so it can't drift
between them.

Margin distance is computed per-leaf (cv2.distanceTransform against
the leaf mask, normalized to that leaf's own max distance-from-edge),
not a fixed pixel radius - a small or large leaf in frame both get a
proportionally-sized margin band this way, rather than one detector
needing a different fixed-pixel constant per photo scale.
"""

from detector_common import compute_leaf_mask, confidence_from_ratio, require_cv2, cv2, np
from discoloration import detect_dark_patches


def _empty_result():
    return {"detected": False, "affected_margin_pct": 0.0, "confidence": 0.0}


def detect_leaf_scorch(bgr_image, cfg):
    """
    cfg keys (see config.py's DEFAULT_CONFIG["detectors"]["leaf_scorch"]):
    saturation_max/value_max - passed straight through to
        discoloration.detect_dark_patches(), same meaning as
        necrosis.py's own config keys.
    margin_band_fraction - fraction (0.0-1.0) of this leaf's own max
        distance-from-edge that counts as "margin" - e.g. 0.15 means
        the outer 15% (by distance, not by area) of the leaf.
    affected_threshold_pct - % of the MARGIN band (not the whole leaf)
        needed before `detected` flips True - see affected_margin_pct.
    """
    require_cv2()
    leaf_mask = compute_leaf_mask(bgr_image)
    leaf_bool = leaf_mask > 0
    if not np.any(leaf_bool):
        return _empty_result()

    distance = cv2.distanceTransform(leaf_mask, cv2.DIST_L2, 5)
    max_distance = float(distance[leaf_bool].max())
    if max_distance <= 0:
        return _empty_result()

    margin_band_max_distance = cfg["margin_band_fraction"] * max_distance
    margin_mask = leaf_bool & (distance <= margin_band_max_distance)
    margin_area = int(np.count_nonzero(margin_mask))
    if margin_area == 0:
        return _empty_result()

    patch_mask = detect_dark_patches(bgr_image, leaf_mask, cfg["saturation_max"], cfg["value_max"])
    scorch_candidate = (patch_mask > 0) & margin_mask
    affected = int(np.count_nonzero(scorch_candidate))
    pct = 100.0 * affected / margin_area
    detected = bool(pct >= cfg["affected_threshold_pct"])
    return {
        "detected": detected,
        "affected_margin_pct": float(pct),
        "confidence": float(confidence_from_ratio(pct, cfg["affected_threshold_pct"])),
    }
