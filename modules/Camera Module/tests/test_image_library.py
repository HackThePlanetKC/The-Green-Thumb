"""
test_image_library.py - stub-based tests for image_library.py: working-
slot rotation (Current/Most Recent/Reference), pin/unpin with cap
enforcement, thumbnail downsampling, and the crop_and_save() color-crop
utility. Requires Pillow (see image_library.py's own module docstring).

Run: python3 test_image_library.py
"""

import os
import sys
import tempfile
from io import BytesIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402

from image_library import ImageLibrary, crop_and_save  # noqa: E402

failures = []


def check(label, cond):
    status = "PASS" if cond else "FAIL"
    print("[{}] {}".format(status, label))
    if not cond:
        failures.append(label)


def make_jpeg(path, color=(0, 200, 0), size=(40, 40)):
    Image.new("RGB", size, color).save(path, "JPEG")


def approx_color(actual, expected, tolerance=8):
    """JPEG is lossy - even a plain shutil.copyfile()'d file was already quantized when the test fixture itself was first saved as JPEG, so pixel colors are compared within a small tolerance, never for exact equality."""
    return all(abs(a - e) <= tolerance for a, e in zip(actual, expected))


with tempfile.TemporaryDirectory() as d:
    lib = ImageLibrary(data_dir=d)

    # --- fresh zone: nothing exists yet ---
    empty = lib.list_zone("A1B2C3")
    check("a fresh zone has no current/most_recent/reference", empty["current"] is None and empty["most_recent"] is None and empty["reference"] is None)
    check("a fresh zone has no saved images", empty["saved"] == {})
    check("image_path() for a non-existent slot returns None", lib.image_path("A1B2C3", "current") is None)

    # --- record_capture: rotation ---
    src1 = os.path.join(d, "src1.jpg")
    make_jpeg(src1, color=(0, 200, 0))
    lib.record_capture("A1B2C3", src1)
    after_first = lib.list_zone("A1B2C3")
    check("after the first capture, current exists", after_first["current"] is not None)
    check("after the first capture, most_recent is still None (nothing to rotate yet)", after_first["most_recent"] is None)

    src2 = os.path.join(d, "src2.jpg")
    make_jpeg(src2, color=(200, 0, 0))
    lib.record_capture("A1B2C3", src2)
    after_second = lib.list_zone("A1B2C3")
    check("after the second capture, most_recent now exists (rotated from the old current)", after_second["most_recent"] is not None)
    check("most_recent's captured_at matches the FIRST capture's timestamp, not the second's", after_second["most_recent"]["captured_at"] == after_first["current"]["captured_at"])
    check("current's captured_at was updated to the second capture", after_second["current"]["captured_at"] != after_first["current"]["captured_at"])

    current_path = lib.image_path("A1B2C3", "current")
    most_recent_path = lib.image_path("A1B2C3", "most_recent")
    check("current now holds the SECOND source image's bytes", approx_color(Image.open(current_path).getpixel((0, 0)), (200, 0, 0)))
    check("most_recent now holds the FIRST source image's bytes", approx_color(Image.open(most_recent_path).getpixel((0, 0)), (0, 200, 0)))

    # a third capture rotates again - most_recent becomes what was current
    src3 = os.path.join(d, "src3.jpg")
    make_jpeg(src3, color=(0, 0, 200))
    lib.record_capture("A1B2C3", src3)
    check("a third capture rotates most_recent to the second capture's image", approx_color(Image.open(lib.image_path("A1B2C3", "most_recent")).getpixel((0, 0)), (200, 0, 0)))
    check("a third capture makes current the third image", approx_color(Image.open(lib.image_path("A1B2C3", "current")).getpixel((0, 0)), (0, 0, 200)))

    # --- record_reference: no rotation, just overwrite ---
    ref_src = os.path.join(d, "ref.jpg")
    make_jpeg(ref_src, color=(50, 50, 50))
    lib.record_reference("A1B2C3", ref_src)
    check("record_reference() populates the reference slot", lib.list_zone("A1B2C3")["reference"] is not None)
    check("record_reference() does not touch most_recent", approx_color(Image.open(lib.image_path("A1B2C3", "most_recent")).getpixel((0, 0)), (200, 0, 0)))

    ref_src2 = os.path.join(d, "ref2.jpg")
    make_jpeg(ref_src2, color=(90, 90, 90))
    lib.record_reference("A1B2C3", ref_src2)
    check("a second record_reference() overwrites the first, no rotation/history kept", approx_color(Image.open(lib.image_path("A1B2C3", "reference")).getpixel((0, 0)), (90, 90, 90)))

    # --- per-zone isolation ---
    check("a different zone has its own independent (empty) state", lib.list_zone("D4E5F6")["current"] is None)

    # --- save_image (pin) + cap enforcement ---
    id1 = lib.save_image("A1B2C3", "current", max_saved_images=2)
    check("save_image() returns a new image id", isinstance(id1, str) and len(id1) > 0)
    check("a pinned image appears in list_zone()'s saved dict", id1 in lib.list_zone("A1B2C3")["saved"])
    check("a pinned image's metadata records its source slot", lib.list_zone("A1B2C3")["saved"][id1]["source"] == "current")

    id2 = lib.save_image("A1B2C3", "most_recent", max_saved_images=2)
    check("a second pin (at, not over, the cap) succeeds", len(lib.list_zone("A1B2C3")["saved"]) == 2)

    try:
        lib.save_image("A1B2C3", "reference", max_saved_images=2)
        raised_at_cap = False
    except ValueError:
        raised_at_cap = True
    check("pinning beyond the cap raises ValueError, blocking the save", raised_at_cap)
    check("a blocked pin does not silently evict an existing pinned image (item 3)", len(lib.list_zone("A1B2C3")["saved"]) == 2)

    # unpinning one frees a slot under the same cap
    lib.unpin_image("A1B2C3", id1)
    check("unpin_image() removes it from the saved dict", id1 not in lib.list_zone("A1B2C3")["saved"])
    id3 = lib.save_image("A1B2C3", "reference", max_saved_images=2)
    check("pinning after unpinning (back under the cap) succeeds", id3 in lib.list_zone("A1B2C3")["saved"])

    try:
        lib.unpin_image("A1B2C3", "not-a-real-id")
        raised_unknown_unpin = False
    except ValueError:
        raised_unknown_unpin = True
    check("unpinning an unknown image id raises ValueError", raised_unknown_unpin)

    try:
        lib.save_image("A1B2C3", "current", max_saved_images=0)
        raised_zero_cap = False
    except ValueError:
        raised_zero_cap = True
    check("a max_saved_images of 0 blocks every pin attempt", raised_zero_cap)

    try:
        lib.save_image("D4E5F6", "current", max_saved_images=5)  # D4E5F6 has no current image at all
        raised_no_source = False
    except ValueError:
        raised_no_source = True
    check("pinning a working slot that doesn't exist yet raises ValueError", raised_no_source)

    # --- thumbnail_bytes ---
    thumb = lib.thumbnail_bytes("A1B2C3", "current", max_dimension=16)
    thumb_img = Image.open(BytesIO(thumb))
    check("thumbnail_bytes() downsamples to within the requested max dimension", max(thumb_img.size) <= 16)
    check("thumbnail_bytes() preserves aspect ratio (source was square)", thumb_img.size[0] == thumb_img.size[1])
    check("thumbnail_bytes() for a non-existent image returns None", lib.thumbnail_bytes("D4E5F6", "current") is None)

    thumb_saved = lib.thumbnail_bytes("A1B2C3", "saved", image_id=id3, max_dimension=16)
    check("thumbnail_bytes() also works against a pinned/saved image", thumb_saved is not None)

    # --- crop_and_save ---
    crop_dest = os.path.join(d, "crop.jpg")
    crop_and_save(src1, (5, 5, 25, 15), crop_dest)
    cropped_img = Image.open(crop_dest)
    check("crop_and_save() produces a crop of the requested pixel dimensions", cropped_img.size == (20, 10))

    # --- image_path validation ---
    try:
        lib.image_path("A1B2C3", "saved")  # missing image_id
        raised_missing_id = False
    except ValueError:
        raised_missing_id = True
    check("image_path(kind='saved') without an image_id raises ValueError", raised_missing_id)

    try:
        lib.image_path("A1B2C3", "not_a_real_kind")
        raised_bad_kind = False
    except ValueError:
        raised_bad_kind = True
    check("image_path() with an unknown kind raises ValueError", raised_bad_kind)

print()
if failures:
    print("{} check(s) failed: {}".format(len(failures), failures))
    sys.exit(1)
else:
    print("All checks passed.")
