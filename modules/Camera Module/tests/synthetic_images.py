"""
synthetic_images.py - Shared synthetic-image helpers for the six
detector test files (test_chlorosis.py, test_necrosis.py,
test_spotting.py, test_leaf_scorch.py, test_powdery_mildew.py,
test_pest_indicators.py).

NOT a test file itself (no `check()`/failure-tracking, nothing to run
standalone) - a helper module the six import from, so the same
"leaf on a background" canvas construction isn't duplicated six times.
Requires numpy + opencv-python-headless, same as the detector modules
themselves (see detector_common.py's own module docstring).

BGR colors used throughout (OpenCV's native channel order, matching
what cv2.imread()/cv2.cvtColor() work with in every detector module).
"""

import numpy as np

# A background clearly outside compute_leaf_mask's default leaf range
# (near-white: low saturation, high value) - see detector_common.py.
BACKGROUND_BGR = (250, 250, 250)

# Verified via HSV conversion to land inside the default DEFAULT_CONFIG
# detector thresholds (config.py) - see the comment on each constant.
HEALTHY_GREEN_BGR = (40, 160, 40)        # HSV ~(60, 191, 160) - within chlorosis's green_hue_min/max (35-85)
YELLOW_CHLOROTIC_BGR = (40, 220, 230)    # HSV ~(28, 211, 230) - below the green band, high saturation
NECROTIC_GREY_BROWN_BGR = (80, 75, 70)   # HSV ~(105, 32, 80) - low saturation, low-mid value
NECROTIC_BLACK_BGR = (10, 10, 10)        # HSV ~(0, 0, 10) - low saturation, low value
WHITE_MILDEW_BGR = (235, 235, 235)       # HSV ~(0, 0, 235) - low saturation, high value
RED_OUTLIER_BGR = (30, 30, 180)          # far from healthy green in LAB space (~123 distance)


def solid_canvas(width, height, bgr):
    canvas = np.zeros((height, width, 3), dtype=np.uint8)
    canvas[:, :] = bgr
    return canvas


def leaf_on_background(width, height, leaf_bgr, background_bgr=BACKGROUND_BGR, margin=10):
    """
    A rectangular "leaf" region inset by `margin` pixels on a
    background canvas - gives compute_leaf_mask() a real interior-vs-
    margin distinction to work with (needed for leaf_scorch.py's
    distance-from-edge weighting; a full-frame solid leaf color has no
    "edge" at all within the frame).
    """
    canvas = solid_canvas(width, height, background_bgr)
    canvas[margin:height - margin, margin:width - margin] = leaf_bgr
    return canvas


def paint_region(canvas, top, left, bottom, right, bgr):
    """Overwrites a rectangular region of `canvas` in place with `bgr`. Returns canvas for chaining."""
    canvas[top:bottom, left:right] = bgr
    return canvas


def paint_checker_texture(canvas, top, left, bottom, right, bgr_a, bgr_b, cell=2):
    """
    Fills a rectangular region with an alternating bgr_a/bgr_b
    checkerboard at `cell`-pixel resolution - a simple, deterministic
    way to produce real per-pixel high-frequency texture (a non-zero
    Laplacian response across the WHOLE region, not just at its outer
    boundary), for powdery_mildew.py's combined color+texture test.
    """
    for y in range(top, bottom):
        for x in range(left, right):
            use_a = ((y // cell) + (x // cell)) % 2 == 0
            canvas[y, x] = bgr_a if use_a else bgr_b
    return canvas


def scatter_dots(canvas, count, dot_radius, bgr, region, rng, min_gap=3):
    """
    Places `count` small filled circles of `bgr` at non-overlapping-ish
    random positions within `region` (top, left, bottom, right) - used
    for spotting.py's discrete lesions, pest_indicators.py's
    pest_clusters, and its stippling sub-check. `rng` is a
    numpy.random.default_rng() instance, passed in so each test
    controls its own determinism/seed rather than this helper hiding
    randomness from the caller.
    """
    import cv2

    top, left, bottom, right = region
    placed = []
    attempts = 0
    while len(placed) < count and attempts < count * 20:
        attempts += 1
        cx = int(rng.integers(left + dot_radius, right - dot_radius))
        cy = int(rng.integers(top + dot_radius, bottom - dot_radius))
        if any(abs(cx - px) < min_gap and abs(cy - py) < min_gap for px, py in placed):
            continue
        cv2.circle(canvas, (cx, cy), dot_radius, bgr, -1)
        placed.append((cx, cy))
    return canvas


def draw_web_lines(canvas, count, region, rng, thread_bgr=(20, 20, 20)):
    """Draws `count` thin, high-contrast line segments within `region` - a simple proxy for webbing's thread-like texture pattern."""
    import cv2

    top, left, bottom, right = region
    for _ in range(count):
        x1, y1 = int(rng.integers(left, right)), int(rng.integers(top, bottom))
        x2, y2 = int(rng.integers(left, right)), int(rng.integers(top, bottom))
        cv2.line(canvas, (x1, y1), (x2, y2), thread_bgr, 1)
    return canvas
