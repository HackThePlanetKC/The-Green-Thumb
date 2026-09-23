"""
test_drama_level.py - stub-based tests for
drama_level.DramaLevelManager, against a throwaway data_dir, synthetic
GrayscaleImage data only.

Run: python3 test_drama_level.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from drama_level import DramaLevelManager  # noqa: E402
from image_compare import GrayscaleImage  # noqa: E402

failures = []


def check(label, cond):
    status = "PASS" if cond else "FAIL"
    print("[{}] {}".format(status, label))
    if not cond:
        failures.append(label)


def flat_image(width, height, value):
    return GrayscaleImage(width, height, [value] * (width * height))


with tempfile.TemporaryDirectory() as d:
    mgr = DramaLevelManager(data_dir=d)

    first = mgr.compute_drama_level("A1B2C3", flat_image(10, 10, 200))
    check("the very first capture for a base returns None - nothing to compare against yet (item 11)", first is None)

    second = mgr.compute_drama_level("A1B2C3", flat_image(10, 10, 200))
    check("an identical second capture gives zero drama level", second == 0.0)

    third = mgr.compute_drama_level("A1B2C3", flat_image(10, 10, 0))
    check("a structurally different capture gives a nonzero drama level", third is not None and third > 0.0)

    fourth = mgr.compute_drama_level("A1B2C3", flat_image(10, 10, 0))
    check("rolling comparison is against the immediately PREVIOUS capture, not the original - identical to the previous (3rd) capture gives zero again", fourth == 0.0)

    # per-base isolation - a second base's history doesn't affect the first
    check("a different base's first capture also returns None independently", mgr.compute_drama_level("D4E5F6", flat_image(5, 5, 100)) is None)
    check("first base's rolling state is untouched by the second base's captures", mgr.compute_drama_level("A1B2C3", flat_image(10, 10, 0)) == 0.0)

print()
if failures:
    print("{} check(s) failed: {}".format(len(failures), failures))
    sys.exit(1)
else:
    print("All checks passed.")
