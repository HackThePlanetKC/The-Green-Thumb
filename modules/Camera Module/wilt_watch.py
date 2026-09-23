"""
wilt_watch.py - Opt-in per-base wilt-level tracking against a stored
reference "healthy" image.

When wilt_watch is first enabled for a base (see per_base_settings.py),
that base has no reference image yet - wilt level cannot be computed
until one exists (item 9 of this task). This module owns exactly that:
capturing/storing the reference crop for a base's region, and computing
subsequent wilt levels against it via image_compare.structural_difference
(shape/position-based - droop/turgor, not color, see that module's
docstring).

Reference images are stored locally on this module's own filesystem as
plain JSON (image_compare.save_grayscale) - NOT published over MQTT,
per this task's explicit requirement (the separately-planned, not-yet-
built image-passthrough option is the only path that would ever put
image data on the wire, and that's out of scope here).

Doesn't decide WHEN a reference gets captured or which image bytes to
use - that's the caller's job (web_portal.py's grid/settings routes,
using camera_capture.py for the actual shot and grid_config.py to crop
the right region). This module only owns storage + the compare step.
"""

import os

from image_compare import load_grayscale, save_grayscale, structural_difference

DEFAULT_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


class WiltWatchManager:
    def __init__(self, per_base_settings_manager, data_dir=DEFAULT_DATA_DIR):
        """
        per_base_settings_manager: a PerBaseSettingsManager (or
        anything exposing clear_wilt_watch_config_necessary(base_id))
        - injected so capture_reference() can clear the
        config_necessary flag in the same place the reference is
        actually stored, rather than every caller having to remember
        to clear it separately.
        """
        self._per_base_settings = per_base_settings_manager
        self._data_dir = data_dir

    def _reference_path(self, base_id):
        return os.path.join(self._data_dir, "wilt_reference_{}.json".format(base_id))

    def has_reference(self, base_id):
        return os.path.exists(self._reference_path(base_id))

    def capture_reference(self, base_id, image):
        """
        Stores `image` (a GrayscaleImage, already cropped to this
        base's region - see grid_config.py) as base_id's wilt-watch
        reference, overwriting any previous one, and clears
        wilt_watch_config_necessary for this base.
        """
        os.makedirs(self._data_dir, exist_ok=True)
        save_grayscale(image, self._reference_path(base_id))
        self._per_base_settings.clear_wilt_watch_config_necessary(base_id)

    def compute_wilt_level(self, base_id, current_image):
        """
        Returns a structural difference score (see
        image_compare.structural_difference) between base_id's stored
        reference and current_image, or None if no reference exists
        yet - callers must not compute/publish a wilt level in that
        case (item 9), and should instead surface the
        wilt_watch_config_necessary prompt (see per_base_settings.py).
        """
        if not self.has_reference(base_id):
            return None
        reference = load_grayscale(self._reference_path(base_id))
        return structural_difference(reference, current_image)
