"""
discoloration.py - Shared low-saturation dark/brown/grey/black patch
detection, within a leaf mask.

The one building block necrosis.py and leaf_scorch.py both need (per
this task's item 4: "leaf scorch reuses necrosis-style brown/tan
discoloration detection") - they differ only in WHERE they look for
it: necrosis measures it across the whole leaf area, uniformly;
leaf_scorch weights it by distance from the leaf's margin (see that
file). The color/threshold logic itself lives here once so it can't
drift between the two.
"""

from detector_common import require_cv2, cv2


def detect_dark_patches(bgr_image, leaf_mask, saturation_max, value_max):
    """
    Returns a uint8 mask (255/0) of pixels within leaf_mask that are
    low-saturation and low-to-mid value - the "dark/brown/grey/black"
    signature of dead or dying tissue, as opposed to chlorosis's
    yellow (moderate-to-high saturation) or powdery mildew's near-
    white (low saturation, HIGH value) signatures. Saturation alone
    doesn't distinguish brown/black from mildew's white/grey, which is
    why value_max matters here (mildew is high-value, this is capped
    low-to-mid).

    Known limitation: leaf_mask itself (compute_leaf_mask()) already
    excludes near-black pixels as background (deep shadow), so truly
    black necrotic/dead tissue - not just dark grey/brown - can end up
    excluded from leaf_mask before this function ever sees it, and
    would be indistinguishable from shadow either way. Accepted for
    this heuristic starting point; see test_necrosis.py for a
    demonstration of the color range that does still work.
    """
    require_cv2()
    hsv = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)
    dark = (s <= saturation_max) & (v <= value_max)
    dark_mask = dark.astype("uint8") * 255
    return cv2.bitwise_and(dark_mask, leaf_mask)
