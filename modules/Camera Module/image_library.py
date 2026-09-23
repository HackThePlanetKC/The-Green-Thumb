"""
image_library.py - Local per-zone photo storage and retention.

A "zone" is an associated base_id (item 1) - same keying as
per_base_settings.py. Per zone, this module owns:

- Three rotating WORKING slots: current (latest capture), most_recent
  (the prior capture), reference (a copy made whenever wilt-watch's
  own reference is (re)captured - see web_portal.py). record_capture()
  rotates current -> most_recent before overwriting current;
  record_reference() just overwrites reference, no rotation.
- An unbounded-by-default-but-capped set of user-pinned SAVED images,
  independent of the three working slots - pinning copies a working
  slot's current bytes into its own permanent file, so a later capture
  rotating "current" never affects an image the user chose to keep.

Deliberately independent storage from wilt_watch.py/drama_level.py,
which persist their OWN separate images for their OWN structural-
comparison math (grayscale only, cropped, not real photos - see those
files). This was an explicit decision, not an oversight: both systems
read from the same original capture at the moment it's taken, then
each persists its own representation for its own consumer (a human
viewing/downloading a real photo here, vs. structural diff math
there) - see decisions-and-practices.md. This module never reads or
writes wilt_watch.py's/drama_level.py's files, and they never read or
write this module's.

Real (color, full-resolution or user-pinned-resolution) JPEGs on disk,
with a small per-zone JSON metadata index (item 4) - captured_at
timestamps and pin status, never image bytes. No image data is ever
published over MQTT by this module itself (see mqtt_presence.py's
publish_thumbnail() for the opt-in downsampled-thumbnail exception,
item 10 - that path reads through this module's thumbnail_bytes()
but the publishing/opt-in decision live there, not here).

Cropping to a zone's region (grid_config.cell_pixel_bbox) and driving
an actual capture (camera_capture.capture_still) are the CALLER's job
(web_portal.py), same "storage owns storage, orchestration lives in
the portal" split already established by wilt_watch.py/drama_level.py
- this module only ever receives an already-cropped, ready-to-store
JPEG file path.
"""

import io
import json
import os
import shutil
import uuid
from datetime import datetime, timezone

try:
    from PIL import Image
except ImportError:  # only required on the real Pi (pip install Pillow) - see image_compare.py's own guard for the same dependency
    Image = None

DEFAULT_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "library")

WORKING_ROLES = ("current", "most_recent", "reference")


def _require_pillow():
    if Image is None:
        raise RuntimeError(
            "Pillow is not installed - this only runs with the real "
            "library present (pip install Pillow). See this module's BUILD.md."
        )


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def crop_and_save(source_path, bbox, dest_path):
    """
    Crops source_path (a real color image file) to bbox (left, top,
    right, bottom - same shape grid_config.cell_pixel_bbox() returns)
    and saves the result as a JPEG at dest_path. Full color, unlike
    image_compare.py's crop helpers, which are grayscale-only and
    exist for a different consumer (wilt_watch.py/drama_level.py's
    structural comparison, not human viewing) - see module docstring.
    """
    _require_pillow()
    with Image.open(source_path) as img:
        cropped = img.convert("RGB").crop(bbox)
        cropped.save(dest_path, "JPEG")


class ImageLibrary:
    def __init__(self, data_dir=DEFAULT_DATA_DIR):
        self._data_dir = data_dir

    # --- paths ---

    def _zone_dir(self, zone):
        return os.path.join(self._data_dir, zone)

    def _saved_dir(self, zone):
        return os.path.join(self._zone_dir(zone), "saved")

    def _slot_path(self, zone, role):
        return os.path.join(self._zone_dir(zone), "{}.jpg".format(role))

    def _saved_path(self, zone, image_id):
        return os.path.join(self._saved_dir(zone), "{}.jpg".format(image_id))

    def _index_path(self, zone):
        return os.path.join(self._zone_dir(zone), "index.json")

    def _ensure_zone_dir(self, zone):
        os.makedirs(self._saved_dir(zone), exist_ok=True)  # also creates the zone dir itself

    def _load_index(self, zone):
        try:
            with open(self._index_path(zone)) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_index(self, zone, index):
        self._ensure_zone_dir(zone)
        tmp_path = self._index_path(zone) + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(index, f)
        os.replace(tmp_path, self._index_path(zone))

    # --- working slots ---

    def record_capture(self, zone, source_path, captured_at=None):
        """
        Rotates zone's working slots: whatever was "current" becomes
        "most_recent" (bytes AND metadata carried over together, so
        most_recent's captured_at always reflects when THAT photo was
        actually taken, not when it was demoted), then source_path is
        copied in as the new "current". A zone's very first capture
        has nothing to rotate - most_recent simply doesn't exist yet
        (see list_zone()/image_path(), both of which treat a missing
        slot as "not available" rather than an error).
        """
        self._ensure_zone_dir(zone)
        index = self._load_index(zone)

        current_path = self._slot_path(zone, "current")
        if os.path.exists(current_path):
            shutil.copyfile(current_path, self._slot_path(zone, "most_recent"))
            if "current" in index:
                index["most_recent"] = index["current"]

        shutil.copyfile(source_path, current_path)
        index["current"] = {"captured_at": captured_at or _now_iso()}
        self._save_index(zone, index)

    def record_reference(self, zone, source_path, captured_at=None):
        """Overwrites zone's "reference" working slot - no rotation, this is a deliberate re-baseline, not a new capture in the current/most_recent sense (see module docstring)."""
        self._ensure_zone_dir(zone)
        shutil.copyfile(source_path, self._slot_path(zone, "reference"))
        index = self._load_index(zone)
        index["reference"] = {"captured_at": captured_at or _now_iso()}
        self._save_index(zone, index)

    # --- pinned/saved images ---

    def save_image(self, zone, source, max_saved_images):
        """
        Pins a copy of one of zone's working slots (source: "current",
        "most_recent", or "reference") into the permanent saved set,
        independent of that slot's own future rotation/overwriting
        (item 2). max_saved_images is the CALLER-supplied cap (from
        per_base_settings.py's own per-zone setting - this module has
        no config dependency of its own, see module docstring) -
        raises ValueError, changing nothing, once the cap is reached
        rather than silently evicting an image the user chose to keep
        (item 3: block, don't silently delete). Returns the new pinned
        image's id.
        """
        if source not in WORKING_ROLES:
            raise ValueError("unknown source: {}".format(source))

        source_path = self._slot_path(zone, source)
        if not os.path.exists(source_path):
            raise ValueError("no {} image exists yet for {}".format(source, zone))

        index = self._load_index(zone)
        saved = index.setdefault("saved", {})
        if len(saved) >= max_saved_images:
            raise ValueError(
                "{} has reached its saved-image limit ({}) - unpin an image first".format(zone, max_saved_images)
            )

        image_id = uuid.uuid4().hex[:12]
        self._ensure_zone_dir(zone)
        shutil.copyfile(source_path, self._saved_path(zone, image_id))
        saved[image_id] = {
            "captured_at": index.get(source, {}).get("captured_at"),
            "pinned_at": _now_iso(),
            "source": source,
        }
        self._save_index(zone, index)
        return image_id

    def unpin_image(self, zone, image_id):
        """Removes a pinned image (file + metadata). Raises ValueError for an unknown id rather than silently no-op-ing, so a stale/mistyped request surfaces as an error."""
        index = self._load_index(zone)
        saved = index.get("saved", {})
        if image_id not in saved:
            raise ValueError("no saved image {} for {}".format(image_id, zone))

        path = self._saved_path(zone, image_id)
        if os.path.exists(path):
            os.remove(path)
        del saved[image_id]
        self._save_index(zone, index)

    # --- reading ---

    def list_zone(self, zone):
        """
        Returns {"current": {...} | None, "most_recent": {...} | None,
        "reference": {...} | None, "saved": {image_id: {...}, ...}} -
        a working slot is None if that role has never been captured
        (fresh install, or a zone with wilt-watch never enabled, for
        "reference"), never a stale/dangling entry pointing at a
        deleted file - both metadata AND file-existence are checked
        together, so a manually-deleted file behind the index's back
        doesn't get reported as still present.
        """
        index = self._load_index(zone)
        result = {}
        for role in WORKING_ROLES:
            path = self._slot_path(zone, role)
            result[role] = dict(index[role]) if role in index and os.path.exists(path) else None
        result["saved"] = {
            image_id: dict(meta)
            for image_id, meta in index.get("saved", {}).items()
            if os.path.exists(self._saved_path(zone, image_id))
        }
        return result

    def image_path(self, zone, kind, image_id=None):
        """
        Resolves (zone, kind, image_id) to a real file path, or None
        if that image doesn't exist - used by web_portal.py's download
        route (item 11) and thumbnail_bytes() below. kind is one of
        WORKING_ROLES, or "saved" (image_id required in that case).
        """
        if kind == "saved":
            if not image_id:
                raise ValueError("image_id is required for kind='saved'")
            path = self._saved_path(zone, image_id)
        elif kind in WORKING_ROLES:
            path = self._slot_path(zone, kind)
        else:
            raise ValueError("unknown image kind: {}".format(kind))

        return path if os.path.exists(path) else None

    def thumbnail_bytes(self, zone, kind="current", image_id=None, max_dimension=320):
        """
        Returns a downsampled JPEG (longest side <= max_dimension,
        aspect ratio preserved) as in-memory bytes, or None if the
        requested image doesn't exist - never writes a thumbnail file
        to disk (item 10's MQTT passthrough is the only consumer,
        published directly from these bytes - see mqtt_presence.py).
        """
        _require_pillow()
        path = self.image_path(zone, kind, image_id=image_id)
        if path is None:
            return None
        with Image.open(path) as img:
            img = img.convert("RGB")
            img.thumbnail((max_dimension, max_dimension))
            buf = io.BytesIO()
            img.save(buf, "JPEG")
            return buf.getvalue()
