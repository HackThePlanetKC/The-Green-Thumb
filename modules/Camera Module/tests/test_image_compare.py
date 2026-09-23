"""
test_image_compare.py - stub-based tests for image_compare.py, using
plain synthetic GrayscaleImage data - no real image file or Pillow
required (load_grayscale_from_file(), the one function that needs
Pillow, isn't exercised here - see that function's own guard).

Run: python3 test_image_compare.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from image_compare import GrayscaleImage, load_grayscale, save_grayscale, structural_difference  # noqa: E402

failures = []


def check(label, cond):
    status = "PASS" if cond else "FAIL"
    print("[{}] {}".format(status, label))
    if not cond:
        failures.append(label)


def solid_image(width, height, value):
    return GrayscaleImage(width, height, [value] * (width * height))


def silhouette_image(width, height, dark_start_row, dark_value=0, background_value=255):
    """A synthetic 'plant' image: every column is background_value above dark_start_row, dark_value at and below it - a flat 'silhouette top edge' at a known row, for deterministic profile comparison."""
    pixels = []
    for y in range(height):
        row_value = dark_value if y >= dark_start_row else background_value
        pixels.extend([row_value] * width)
    return GrayscaleImage(width, height, pixels)


# --- GrayscaleImage basics ---
try:
    GrayscaleImage(2, 2, [1, 2, 3])  # wrong length
    raised = False
except ValueError:
    raised = True
check("GrayscaleImage rejects a pixel list of the wrong length", raised)

img = GrayscaleImage(3, 2, [1, 2, 3, 4, 5, 6])
check("get(x, y) indexes row-major", img.get(0, 0) == 1 and img.get(2, 0) == 3 and img.get(0, 1) == 4)

cropped = img.crop(1, 0, 3, 2)
check("crop() returns the correct sub-region", cropped.width == 2 and cropped.height == 2 and cropped.pixels == [2, 3, 5, 6])

# --- save/load round trip ---
with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, "ref.json")
    save_grayscale(img, path)
    loaded = load_grayscale(path)
    check("save_grayscale()/load_grayscale() round-trips width/height/pixels", (loaded.width, loaded.height, loaded.pixels) == (img.width, img.height, img.pixels))

# --- structural_difference: identical images ---
a = silhouette_image(10, 20, dark_start_row=5)
b = silhouette_image(10, 20, dark_start_row=5)
check("identical images have zero structural difference", structural_difference(a, b) == 0.0)

# --- structural_difference: shifted silhouette ---
a = silhouette_image(10, 20, dark_start_row=2)
b = silhouette_image(10, 20, dark_start_row=5)
diff = structural_difference(a, b)
check("a shifted silhouette produces a positive difference", diff > 0.0)
check("difference matches the expected normalized row shift (3/20)", abs(diff - (3 / 20.0)) < 1e-9)

# --- structural_difference: all-background images (no dark pixel found) still compare cleanly ---
a = solid_image(5, 5, 255)
b = solid_image(5, 5, 255)
check("two all-background images have zero difference", structural_difference(a, b) == 0.0)

# --- structural_difference: mismatched widths raise ---
a = GrayscaleImage(5, 5, [0] * 25)
b = GrayscaleImage(6, 5, [0] * 30)
try:
    structural_difference(a, b)
    raised = False
except ValueError:
    raised = True
check("comparing images of different widths raises ValueError", raised)

print()
if failures:
    print("{} check(s) failed: {}".format(len(failures), failures))
    sys.exit(1)
else:
    print("All checks passed.")
