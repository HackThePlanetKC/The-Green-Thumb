"""
pest_indicators.py - Heuristic pest-indicator detection.

Per this task's item 6: ONE grouped opt-in (see per_base_settings.py -
"pest_indicators" is a single toggle, not three), covering three
independent sub-checks, each reported separately so the result can say
WHICH pattern triggered:

  a. webbing        - fine, high-contrast thread-like texture
  b. pest_clusters   - many small clustered color-outlier blobs
  c. stippling       - fine light speckling

Confidence is capped below other detectors' ceiling for all three sub-
checks (see detector_common.confidence_from_ratio's `cap` parameter) -
per item 7, dust, fibers, and ordinary leaf texture can all trigger
signals structurally similar to webbing/stippling, so even a strong
raw signal here is deliberately reported as less certain than an
equally strong signal from, say, chlorosis's color-only measurement.
This is a documented, conservative choice, not a claim that stronger
underlying evidence doesn't exist.

Distinguishing stippling from powdery_mildew.py (both are "light
speckling" and can visually overlap - item 6c asks this to be
documented clearly):

  - powdery_mildew.py requires BOTH a light color signal AND a texture
    (Laplacian) signal together, and doesn't care how the resulting
    area is shaped - a mildew coating is treated as one or a few
    fairly CONTIGUOUS regions.
  - stippling (here) uses ONLY a color signal (no texture/Laplacian
    check at all - individual stipples are often flat/textureless,
    just small and light) and instead distinguishes itself by
    CONNECTED-COMPONENT SIZE: many small, separate, discrete light
    specks rather than one contiguous coated area.

  In practice a light dusty coating and a dense field of tiny light
  dots can look similar in a photo, and this pair of heuristics will
  sometimes call one what a human would call the other - which is
  exactly the kind of case the disclaimer (visual_disclaimers.py)
  exists for.

pest_clusters reuses detector_common.color_outlier_mask() - the same
LAB color-outlier primitive spotting.py uses - but tuned the opposite
direction: spotting.py wants a FEW LARGER lesions (high min-area, no
count requirement); pest_clusters wants MANY SMALL objects (low
max-area, a minimum count requirement). No spatial-proximity/
clustering check (e.g. nearest-neighbor distance between components)
is implemented in this v1 - component count and per-component size are
used as a simpler proxy for "clustered," which is a real
simplification, documented rather than silently assumed; a stricter
spatial check would be a natural refinement once this heuristic has
been tried against real photos.
"""

from detector_common import color_outlier_mask, compute_leaf_mask, confidence_from_ratio, leaf_area, require_cv2, cv2, np

_CONFIDENCE_CAP = 0.7


def _empty_sub_result():
    return {"detected": False, "confidence": 0.0}


def _detect_webbing(bgr_image, leaf_mask, total_leaf, cfg):
    """
    Fine, high-contrast thread-like texture: Canny edge density within
    the leaf mask. Webs are many thin, high-contrast lines crossing an
    area - a real but simple v1 proxy is just "how much edge, overall,
    is in this leaf region" rather than trying to detect line shapes
    specifically (e.g. via a Hough line transform), which would add
    real complexity for a signal this heuristic already treats as
    lower-confidence regardless (see _CONFIDENCE_CAP above).
    """
    gray = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, cfg["canny_low"], cfg["canny_high"])
    edges_in_leaf = cv2.bitwise_and(edges, leaf_mask)
    edge_density = float(int(np.count_nonzero(edges_in_leaf)) / total_leaf)
    detected = bool(edge_density >= cfg["edge_density_min"])
    return {
        "detected": detected,
        "edge_density": edge_density,
        "confidence": float(confidence_from_ratio(edge_density, cfg["edge_density_min"], cap=_CONFIDENCE_CAP)),
    }


def _detect_pest_clusters(bgr_image, leaf_mask, total_leaf, cfg):
    """Many small, clustered color-outlier blobs - same color primitive as spotting.py, opposite component-size/count tuning. See module docstring."""
    outlier_mask = color_outlier_mask(bgr_image, leaf_mask, cfg["color_distance_threshold"])
    num_labels, _labels, stats, _centroids = cv2.connectedComponentsWithStats(outlier_mask, connectivity=8)
    small_components = [
        stats[i, cv2.CC_STAT_AREA] for i in range(1, num_labels)
        if stats[i, cv2.CC_STAT_AREA] <= cfg["max_component_area_px"]
    ]
    component_count = int(len(small_components))
    detected = bool(component_count >= cfg["min_component_count"])
    return {
        "detected": detected,
        "component_count": component_count,
        "confidence": float(confidence_from_ratio(component_count, cfg["min_component_count"], cap=_CONFIDENCE_CAP)),
    }


def _detect_stippling(bgr_image, leaf_mask, total_leaf, cfg):
    """Many small, discrete LIGHT specks - color-only (no texture check, unlike powdery_mildew.py). See module docstring for the full mildew/stippling distinction."""
    hsv = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)
    leaf_bool = leaf_mask > 0
    light_candidate = leaf_bool & (s <= cfg["saturation_max"]) & (v >= cfg["value_min"])
    light_mask = (light_candidate.astype(np.uint8)) * 255

    num_labels, _labels, stats, _centroids = cv2.connectedComponentsWithStats(light_mask, connectivity=8)
    small_components = [
        stats[i, cv2.CC_STAT_AREA] for i in range(1, num_labels)
        if stats[i, cv2.CC_STAT_AREA] <= cfg["max_component_area_px"]
    ]
    component_count = int(len(small_components))
    detected = bool(component_count >= cfg["min_component_count"])
    return {
        "detected": detected,
        "component_count": component_count,
        "confidence": float(confidence_from_ratio(component_count, cfg["min_component_count"], cap=_CONFIDENCE_CAP)),
    }


def detect_pest_indicators(bgr_image, cfg):
    """
    cfg: see config.py's DEFAULT_CONFIG["detectors"]["pest_indicators"] -
    nested "webbing"/"pest_clusters"/"stippling" sub-dicts, each with
    its own thresholds (see each _detect_* function above).

    Returns a grouped result: `detected`/`confidence` summarize across
    all three sub-checks, `triggered` lists which one(s) fired, and
    each sub-check's own full result is included under its own key.
    """
    require_cv2()
    leaf_mask = compute_leaf_mask(bgr_image)
    total_leaf = leaf_area(leaf_mask)
    if total_leaf == 0:
        empty = _empty_sub_result()
        return {"detected": False, "triggered": [], "confidence": 0.0, "webbing": empty, "pest_clusters": empty, "stippling": empty}

    webbing = _detect_webbing(bgr_image, leaf_mask, total_leaf, cfg["webbing"])
    pest_clusters = _detect_pest_clusters(bgr_image, leaf_mask, total_leaf, cfg["pest_clusters"])
    stippling = _detect_stippling(bgr_image, leaf_mask, total_leaf, cfg["stippling"])

    sub_results = (("webbing", webbing), ("pest_clusters", pest_clusters), ("stippling", stippling))
    triggered = [name for name, sub in sub_results if sub["detected"]]
    overall_confidence = max((sub["confidence"] for _name, sub in sub_results), default=0.0)

    return {
        "detected": len(triggered) > 0,
        "triggered": triggered,
        "confidence": overall_confidence,
        "webbing": webbing,
        "pest_clusters": pest_clusters,
        "stippling": stippling,
    }
