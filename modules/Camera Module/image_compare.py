"""
image_compare.py - Grayscale image cropping and structural (not
color-based) comparison, for wilt_watch.py and drama_level.py.

GrayscaleImage is this module's own minimal, dependency-free
representation (width, height, a flat list of 0-255 ints) - NOT a real
image file format. The real capture pipeline (rpicam-still via
camera_capture.py, producing a JPEG) is converted into one via
load_grayscale_from_file(), which is the only place PIL is required
(import-guarded, same pattern as ring.py's rpi_ws281x guard) - every
other function here, including every test, works on plain synthetic
GrayscaleImage data with no image library installed at all. Keeping
the comparison math independent of any real image library is what
makes it fully stub-testable per this task's requirements.

structural_difference() is deliberately grayscale-in/grayscale-out and
position-based, not a color classifier - wilt-watch and drama-level
are both about shape/posture (droop, turgor - where the plant's
silhouette sits in the frame), never about leaf color, which is a
separate, not-yet-built health classifier's job (see
docs/ARCHITECTURE.md - keeping color-based health scoring and
structural wilt comparison strictly separate is an explicit project
practice, see decisions-and-practices.md's species-threshold entry for
the same "keep the classifier's job narrow" reasoning applied
elsewhere).

The algorithm below (a per-column "first dark pixel from the top" edge
profile, compared column-by-column) is a starting point, not a
validated-against-real-plants metric - same "tune once pointed at a
real plant, not sourced from a datasheet" spirit as this module's
light/flash thresholds. _EDGE_THRESHOLD is the one tunable knob.
"""

import json

try:
    from PIL import Image
except ImportError:  # only required for load_grayscale_from_file() - see module docstring
    Image = None

# A column pixel darker than this (0-255, PIL "L" mode) is treated as
# the plant's silhouette rather than background. Assumes a reasonably
# light/neutral background behind the plant - a real deployment may
# need this tuned, or a background-subtraction step added later, once
# there's real capture data to look at (not available yet - capture
# scheduling isn't built, see README.md "Remaining").
_EDGE_THRESHOLD = 128


class GrayscaleImage:
    def __init__(self, width, height, pixels):
        if len(pixels) != width * height:
            raise ValueError("pixels length {} does not match {}x{}".format(len(pixels), width, height))
        self.width = width
        self.height = height
        self.pixels = pixels

    def get(self, x, y):
        return self.pixels[y * self.width + x]

    def crop(self, left, top, right, bottom):
        """
        Returns a new GrayscaleImage containing the region [left, right)
        x [top, bottom). Bounds are not clamped - an out-of-range crop
        raises IndexError via get(), the same fail-loud behavior as a
        plain list index rather than silently returning a smaller or
        shifted region than the caller asked for.
        """
        new_width = right - left
        new_height = bottom - top
        pixels = [self.get(left + x, top + y) for y in range(new_height) for x in range(new_width)]
        return GrayscaleImage(new_width, new_height, pixels)

    def to_dict(self):
        return {"width": self.width, "height": self.height, "pixels": self.pixels}

    @classmethod
    def from_dict(cls, data):
        return cls(data["width"], data["height"], data["pixels"])


def save_grayscale(image, path):
    """Persists a GrayscaleImage as JSON - plain JSON for consistency with the rest of this module's storage (config.json, wifi credentials), not a new format. See wilt_watch.py / drama_level.py."""
    with open(path, "w") as f:
        json.dump(image.to_dict(), f)


def load_grayscale(path):
    """Loads a GrayscaleImage previously saved by save_grayscale()."""
    with open(path) as f:
        return GrayscaleImage.from_dict(json.load(f))


def load_grayscale_from_file(path):
    """
    Loads a real image file (e.g. a JPEG from camera_capture.py) and
    converts it to a GrayscaleImage - the one function in this module
    that requires PIL/Pillow (pip install Pillow on the Pi; not
    required for anything else here, including every test).
    """
    if Image is None:
        raise RuntimeError(
            "Pillow is not installed - this only runs with the real "
            "library present (pip install Pillow). See this module's BUILD.md."
        )
    with Image.open(path) as img:
        gray = img.convert("L")
        width, height = gray.size
        pixels = list(gray.getdata())
    return GrayscaleImage(width, height, pixels)


def _column_profile(image):
    """
    Per column, the normalized (0.0-1.0) row index of the first pixel
    from the top darker than _EDGE_THRESHOLD - a simple silhouette
    "where does the plant start" profile. A column with no such pixel
    (all background) profiles as 1.0 (as if the silhouette started at
    the very bottom edge) rather than being excluded, so every column
    contributes to the comparison the same way.
    """
    profile = []
    for x in range(image.width):
        edge_row = image.height  # not found - treated as the bottom edge, see docstring
        for y in range(image.height):
            if image.get(x, y) < _EDGE_THRESHOLD:
                edge_row = y
                break
        profile.append(edge_row / float(image.height))
    return profile


def structural_difference(image_a, image_b):
    """
    Returns a difference score >= 0.0 (0.0 = identical silhouette
    profile) between two same-width GrayscaleImages, comparing their
    per-column edge profiles (see _column_profile) rather than raw
    pixel values - a droop/turgor (shape/position) comparison, not a
    color- or brightness-based one. Used by both wilt_watch.py
    (against a stored reference) and drama_level.py (against the
    immediately previous capture) - the two differ only in which
    second image they pass in, not in how the comparison works.

    Raises ValueError if the two images aren't the same width -
    they're expected to be crops of the same grid cell(s), which are a
    fixed pixel size for a given grid config (see grid_config.py),
    so a width mismatch means the caller passed mismatched regions,
    not a case worth silently coercing.
    """
    if image_a.width != image_b.width:
        raise ValueError("cannot compare images of different widths ({} vs {})".format(image_a.width, image_b.width))

    profile_a = _column_profile(image_a)
    profile_b = _column_profile(image_b)
    return sum(abs(a - b) for a, b in zip(profile_a, profile_b)) / len(profile_a)
