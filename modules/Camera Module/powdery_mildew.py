"""
powdery_mildew.py - Heuristic powdery mildew detection.

Per this task's item 5: white/grey coating detection via TEXTURE
(high-frequency light speckling) COMBINED WITH color (light/white) -
both required, not either alone. Color alone (low saturation, high
value) can't distinguish a genuine dusty mildew coating from a
smooth, naturally pale/silvery leaf surface (many houseplants have
one) or from washed-out overexposure; texture alone can't distinguish
a mildew coating from ordinary leaf-vein texture on a normally-colored
leaf. Requiring both together is what makes this distinct from
chlorosis.py's uniform yellow color shift (which needs no texture
signal at all - chlorosis is a flat color change, not a coating) and
from pest_indicators.py's "stippling" sub-check (light color WITHOUT
a texture requirement - see that file's module docstring for the full
distinguishing logic between mildew and stippling, since both involve
light-colored speckling and can visually overlap).

Texture is measured as per-pixel Laplacian magnitude (OpenCV's
standard "how much high-frequency detail is here" operator) rather
than a windowed local-variance filter - simpler to compute and
threshold, and sufficient for this heuristic's purpose (a real dusty
coating produces many adjacent high-Laplacian pixels, not isolated
ones, so requiring overlap with the color mask across a real area
already provides the "not just noise" filtering a windowed variance
measure would otherwise be doing).
"""

from detector_common import compute_leaf_mask, confidence_from_ratio, leaf_area, require_cv2, cv2, np


def detect_powdery_mildew(bgr_image, cfg):
    """
    cfg keys (see config.py's DEFAULT_CONFIG["detectors"]["powdery_mildew"]):
    saturation_max - upper bound on saturation for the white/grey color
        candidate.
    value_min - lower bound on value (brightness) for the white/grey
        color candidate.
    texture_variance_min - minimum per-pixel Laplacian magnitude to
        count as "high-frequency" texture.
    affected_threshold_pct - % of leaf area needed before `detected`
        flips True.
    """
    require_cv2()
    leaf_mask = compute_leaf_mask(bgr_image)
    total_leaf = leaf_area(leaf_mask)
    if total_leaf == 0:
        return {"detected": False, "affected_area_pct": 0.0, "confidence": 0.0}

    hsv = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)
    leaf_bool = leaf_mask > 0
    light_candidate = leaf_bool & (s <= cfg["saturation_max"]) & (v >= cfg["value_min"])

    gray = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2GRAY)
    laplacian = cv2.Laplacian(gray, cv2.CV_64F, ksize=3)
    high_texture = np.abs(laplacian) >= cfg["texture_variance_min"]

    mildew_candidate = light_candidate & high_texture
    affected = int(np.count_nonzero(mildew_candidate))
    pct = 100.0 * affected / total_leaf
    detected = bool(pct >= cfg["affected_threshold_pct"])
    return {
        "detected": detected,
        "affected_area_pct": float(pct),
        "confidence": float(confidence_from_ratio(pct, cfg["affected_threshold_pct"])),
    }
