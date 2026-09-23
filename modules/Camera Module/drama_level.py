"""
drama_level.py - Opt-in per-base "rate of change" tracking: each
capture's region crop compared against the immediately previous
capture of the same region.

Independent of wilt_watch.py (see per_base_settings.py - enabling one
never implies the other): no reference image, no config_necessary
flow, since there's nothing to configure - the "previous capture" is
just whatever came before, updated every time. Represents rate of
change between consecutive captures, not absolute wilt severity (a
plant that's been wilted and unchanging for days would show a low
drama-level score despite a high wilt-watch score, by design - they
answer different questions).

Same storage/comparison primitives as wilt_watch.py
(image_compare.save_grayscale/load_grayscale/structural_difference) -
the two modules differ only in WHICH second image they keep on disk
(a fixed reference vs. a constantly-overwritten "last capture"), not
in the comparison math itself.
"""

import os

from image_compare import load_grayscale, save_grayscale, structural_difference

DEFAULT_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


class DramaLevelManager:
    def __init__(self, data_dir=DEFAULT_DATA_DIR):
        self._data_dir = data_dir

    def _previous_path(self, base_id):
        return os.path.join(self._data_dir, "drama_previous_{}.json".format(base_id))

    def compute_drama_level(self, base_id, current_image):
        """
        Returns a structural difference score against base_id's
        previous capture, then overwrites the stored "previous" with
        current_image for next time. Returns None on the very first
        call for a base (nothing to compare against yet) rather than
        a 0.0 score, which would misleadingly claim "no change" for a
        capture that was never actually compared to anything.
        """
        os.makedirs(self._data_dir, exist_ok=True)
        path = self._previous_path(base_id)

        score = None
        if os.path.exists(path):
            previous = load_grayscale(path)
            score = structural_difference(previous, current_image)

        save_grayscale(current_image, path)
        return score
