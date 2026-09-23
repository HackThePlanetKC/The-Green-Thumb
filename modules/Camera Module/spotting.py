"""
spotting.py - Heuristic spot/lesion detection.

Localized color-outlier blob detection via connected-component
analysis, per this task's item 3: discrete clusters whose color
deviates from the leaf's own dominant color, tuned for a FEW LARGER
lesions - a higher per-component minimum area, no minimum component
COUNT requirement. That tuning is the one thing distinguishing this
from pest_indicators.py's "pest_clusters" sub-check, which reuses the
same color-outlier approach but tuned the opposite way (many small
components, a minimum count requirement) - see that file.

Color distance is measured in LAB space (via OpenCV's COLOR_BGR2LAB),
not raw BGR or HSV - LAB's perceptual uniformity means a fixed
distance threshold behaves more consistently across different lesion
colors (a red fungal spot and a black bacterial spot can both register
as "far" from a green leaf without needing separate hue-band rules for
each), unlike the other detectors here which each look for one
specific, named color signature (yellow, brown/black, white) and so
use HSV directly.
"""

from detector_common import color_outlier_mask, compute_leaf_mask, confidence_from_ratio, leaf_area, require_cv2, cv2


def detect_spotting(bgr_image, cfg):
    """
    cfg keys (see config.py's DEFAULT_CONFIG["detectors"]["spotting"]):
    color_distance_threshold - minimum LAB Euclidean distance from the
        leaf's own mean color to count a pixel as an outlier.
    min_lesion_area_px - minimum connected-component area to count as
        a real lesion rather than single-pixel noise - deliberately
        larger than pest_indicators.py's cluster/stippling minimums.
    min_lesion_count - number of qualifying components needed before
        `detected` flips True (default 1 - any real lesion counts).
    affected_threshold_pct - used only for the confidence curve, not
        the detected flag itself (lesion_count already gates that).
    """
    require_cv2()
    leaf_mask = compute_leaf_mask(bgr_image)
    total_leaf = leaf_area(leaf_mask)
    if total_leaf == 0:
        return {"detected": False, "lesion_count": 0, "affected_area_pct": 0.0, "confidence": 0.0}

    outlier_mask = color_outlier_mask(bgr_image, leaf_mask, cfg["color_distance_threshold"])

    num_labels, _labels, stats, _centroids = cv2.connectedComponentsWithStats(outlier_mask, connectivity=8)
    lesion_areas = [
        stats[i, cv2.CC_STAT_AREA] for i in range(1, num_labels)  # label 0 is background
        if stats[i, cv2.CC_STAT_AREA] >= cfg["min_lesion_area_px"]
    ]

    lesion_count = int(len(lesion_areas))
    affected = int(sum(lesion_areas))
    pct = 100.0 * affected / total_leaf
    detected = bool(lesion_count >= cfg["min_lesion_count"])
    return {
        "detected": detected,
        "lesion_count": lesion_count,
        "affected_area_pct": float(pct),
        "confidence": float(confidence_from_ratio(pct, cfg["affected_threshold_pct"])),
    }
