"""
chlorosis.py - Heuristic chlorosis (yellowing) detection.

HSV color-space analysis, per this task's item 1: flags leaf-region
pixels shifted toward yellow hue OUTSIDE the image's own expected
green variance - not a fixed absolute yellow hue window. This image's
own healthy-looking green pixels establish a mean/std-dev hue
baseline; pixels shifted well below that (toward yellow, still with
real saturation - not washed-out/bleached) are flagged as chlorotic.
Per-image, not a fixed threshold, so this adapts somewhat to lighting/
white-balance differences between captures rather than assuming one
universal "healthy green" hue works for every photo.

Species-agnostic and single-frame (no reference/previous-capture
comparison - see wilt_watch.py/drama_level.py for that structural,
grayscale-only approach; this is color-based and absolute).
"""

from detector_common import compute_leaf_mask, confidence_from_ratio, leaf_area, require_cv2, cv2, np


def _empty_result():
    return {"detected": False, "affected_area_pct": 0.0, "confidence": 0.0}


def detect_chlorosis(bgr_image, cfg):
    """
    cfg keys (see config.py's DEFAULT_CONFIG["detectors"]["chlorosis"]):
    green_hue_min/green_hue_max - OpenCV hue (0-179) band treated as
        this image's "healthy green" reference population.
    std_dev_multiplier - how many std-devs below the reference green
        hue's mean counts as "shifted toward yellow".
    yellow_hue_floor - lower bound on what still counts as yellow
        chlorosis rather than red/brown/orange (necrosis/scorch
        territory) - keeps this detector from overlapping theirs.
    saturation_min - excludes washed-out/desaturated pixels; chlorosis
        is a real yellow shift, not a loss of all color.
    affected_threshold_pct - % of leaf area needed before `detected`
        flips True.
    """
    require_cv2()
    leaf_mask = compute_leaf_mask(bgr_image)
    total_leaf = leaf_area(leaf_mask)
    if total_leaf == 0:
        return _empty_result()

    hsv = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)
    leaf_bool = leaf_mask > 0

    green_band = leaf_bool & (h >= cfg["green_hue_min"]) & (h <= cfg["green_hue_max"])
    if not np.any(green_band):
        # No clear healthy-green reference population in this frame -
        # there's nothing to measure "expected variance" against, so
        # nothing can be confidently flagged relative to it. Returning
        # "not detected" rather than falling back to a fixed hue
        # window, which would silently abandon the adaptive approach
        # this detector is specifically meant to use.
        return _empty_result()

    green_hues = h[green_band].astype(np.float64)
    mean_h = float(green_hues.mean())
    std_h = float(green_hues.std()) or 1.0  # avoid a zero-std wall on a perfectly uniform synthetic/reference image

    yellow_shift_threshold = mean_h - cfg["std_dev_multiplier"] * std_h
    candidate = (
        leaf_bool
        & (h.astype(np.float64) < yellow_shift_threshold)
        & (h >= cfg["yellow_hue_floor"])
        & (s >= cfg["saturation_min"])
    )
    affected = int(np.count_nonzero(candidate))
    pct = 100.0 * affected / total_leaf
    detected = bool(pct >= cfg["affected_threshold_pct"])
    return {
        "detected": detected,
        "affected_area_pct": float(pct),
        "confidence": float(confidence_from_ratio(pct, cfg["affected_threshold_pct"])),
    }
