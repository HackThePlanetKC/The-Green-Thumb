"""
detector_common.py - Shared utilities for the six heuristic visual
detectors (chlorosis.py, necrosis.py, spotting.py, leaf_scorch.py,
powdery_mildew.py, pest_indicators.py).

Uses OpenCV (`cv2`) + numpy - a new dependency for this module (on top
of rpi_ws281x, paho-mqtt, Pillow). Import-guarded the same way this
module already guards rpi_ws281x (ring.py) and Pillow
(image_compare.py): every function here that needs it raises a clear
RuntimeError naming the install command, rather than failing at import
time, so importing this module never requires OpenCV/numpy to be
installed - only calling its functions does. Unlike ring.py's guard,
though, every detector's TEST also exercises the real cv2-based
algorithm (not a stubbed-out decision layer around it, the way
maybe_flash() tests stub the ring but keep the light-vs-threshold
logic under test) - cv2 processing IS the logic being tested here, so
running these tests for real requires numpy + opencv-python-headless
installed. See this module's own BUILD.md and README.md's Tests
section.

None of these detectors compare against a reference or previous
capture - that's wilt_watch.py/drama_level.py's job (structural,
grayscale-only comparison). These are single-frame, absolute,
color/texture detectors operating on one photo at a time.

Convention every detector follows: every value in a returned result
dict is cast to a native Python type (bool()/int()/float()) before
returning, never left as a numpy scalar (np.bool_, np.float64, ...).
numpy arithmetic/comparisons routinely produce numpy scalar types even
from otherwise-native inputs (e.g. np.count_nonzero()'s return type
has varied across numpy versions) - left uncast, `result["detected"]
is True` can silently be False even when the value prints as `True`
(numpy's boolean scalars aren't Python's `True` by identity), and
json.dumps() cannot serialize numpy scalars at all, which matters once
a result is published over MQTT. Caught via exactly this failure mode
in pest_indicators.py's own tests - see test_pest_indicators.py.
"""

try:
    import cv2
    import numpy as np
except ImportError:  # only required on the real Pi (pip install opencv-python-headless numpy)
    cv2 = None
    np = None

_INSTALL_HINT = (
    "opencv-python-headless/numpy are not installed - detectors only run with "
    "the real libraries present (pip install opencv-python-headless numpy). "
    "See this module's BUILD.md."
)


def require_cv2():
    if cv2 is None or np is None:
        raise RuntimeError(_INSTALL_HINT)


def load_bgr_image_from_file(path):
    """Loads a real image file (e.g. a JPEG from camera_capture.py) as an OpenCV BGR array."""
    require_cv2()
    image = cv2.imread(path)
    if image is None:
        raise RuntimeError("failed to read image at {}".format(path))
    return image


def compute_leaf_mask(bgr_image, near_white_saturation_max=25, near_white_value_min=200, near_black_value_max=20):
    """
    Broad heuristic "is this pixel plant tissue, not background" mask,
    returned as a uint8 array (255 = leaf, 0 = background) the same
    shape as the input image's first two dimensions.

    Deliberately NOT a "green pixels only" mask: a chlorotic (yellowed),
    necrotic (browned/blackened), or mildew-coated (whitened) region is
    still leaf tissue, not background, and every detector below needs
    those regions counted as leaf area to measure a percentage against.
    Instead this excludes only the two background cases every capture
    is expected to have some of: near-white (low saturation, high
    value - pots, walls, paper) and near-black (very low value - deep
    shadow). Everything else, any hue at moderate value/saturation,
    counts as leaf.

    Known limitations, both accepted for this heuristic (not
    diagnostic-grade) starting point:
    - Background clutter that isn't near-white or near-black - a brown
      pot, visible soil, a mid-tone wall - would be misclassified as
      leaf tissue. A future revision could add a fixed region-of-
      interest crop (tying into grid_config.py's per-base cell
      bounding box, which already isolates a base's assigned region)
      or a dedicated segmentation step; not done here to keep this v1
      tractable, same "documented starting point, tune later" spirit
      as this module's other thresholds.
    - The near-white exclusion directly overlaps powdery_mildew.py's
      own target signature (light/white coating color). A mildew
      coating bright/pure-white enough to cross this function's own
      near-white cutoff gets excluded from the leaf mask before
      powdery_mildew.py ever sees it - the same shape of issue as
      necrosis.py's near-black-vs-shadow overlap (see discoloration.py).
      powdery_mildew.py's own config default (value_min=180) stays
      safely below this function's default near_white_value_min (200)
      specifically so realistic "dusty coating" values aren't masked
      out - but a sufficiently blown-out/severe coating still could be.
      See test_powdery_mildew.py for the color range that does work.
    """
    require_cv2()
    hsv = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)
    near_white = (s < near_white_saturation_max) & (v > near_white_value_min)
    near_black = v < near_black_value_max
    leaf = ~(near_white | near_black)
    return (leaf.astype(np.uint8)) * 255


def confidence_from_ratio(value, threshold, saturate_multiplier=2.0, cap=1.0):
    """
    Maps a measured value against the threshold that flips a
    detector's `detected` flag into a 0.0-cap confidence score:
    0.0 at value=0, 0.5 exactly at the threshold (a borderline call),
    rising to `cap` once value reaches threshold*saturate_multiplier.

    `cap` lets a detector deliberately limit its own maximum
    confidence below 1.0 (see pest_indicators.py, which caps its three
    sub-checks lower than other detectors given their documented
    elevated false-positive risk) - a conservative choice reflecting
    that risk, not a claim that stronger evidence doesn't exist.
    """
    if threshold <= 0:
        return cap if value > 0 else 0.0
    ratio = value / (threshold * saturate_multiplier)
    return max(0.0, min(cap, ratio))


def leaf_area(leaf_mask):
    """Count of leaf-mask pixels (255-valued) - shared so every detector counts area the same way."""
    require_cv2()
    return int(np.count_nonzero(leaf_mask))


def color_outlier_mask(bgr_image, leaf_mask, distance_threshold):
    """
    Returns a uint8 mask (255/0) of pixels within leaf_mask whose LAB
    color is at least distance_threshold away from leaf_mask's own
    mean LAB color - shared by spotting.py and pest_indicators.py's
    "pest_clusters" sub-check, which differ only in how they filter/
    count the resulting connected components afterward (few larger
    components vs. many small ones - see each file's own docstring).

    LAB, not raw BGR or HSV: its perceptual uniformity means one fixed
    distance threshold behaves reasonably across differently-colored
    outliers (a red spot and a black spot can both register as "far"
    from a green leaf) without separate per-hue rules, unlike the
    other detectors here, which each look for one specific, named
    color signature and so use HSV directly.
    """
    require_cv2()
    lab = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2LAB).astype(np.float64)
    leaf_bool = leaf_mask > 0
    mean_color = lab[leaf_bool].mean(axis=0)
    distance = np.linalg.norm(lab - mean_color, axis=2)
    outlier = leaf_bool & (distance >= distance_threshold)
    return (outlier.astype(np.uint8)) * 255
