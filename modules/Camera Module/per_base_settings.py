"""
per_base_settings.py - Per-base opt-in metric toggles.

Stored keyed by base_id (config.py's per_base_settings dict), not as a
single flat set of settings - required so a given base's own portal
can only ever read/write its OWN entry, never another base's (the
access-control boundary itself is enforced by the caller, e.g.
web_portal.py only ever passing that base's own base_id for a request
that arrived on that base's scoped route - this manager has no
concept of "who is asking").

general_health is always on and deliberately NOT one of the toggleable
METRICS below or stored in config at all - there's no value in
persisting a setting that can never be anything but True. get_settings()
still reports it (as a constant) so callers rendering a settings page
don't need a special case for "the one metric with no checkbox".

Each of the other metrics is an independent opt-in: enabling one never
implies or enables another (e.g. turning on wilt_watch does not turn
on drama_level, even though both are capture-comparison metrics - see
wilt_watch.py / drama_level.py). chlorosis/necrosis/spotting/
leaf_scorch/powdery_mildew/pest_indicators are the six heuristic
single-frame visual detectors (see each file of the same name, and
detector_common.py) - independent of wilt_watch/drama_level, which
compare against a reference or previous capture instead of analyzing
one frame in isolation.

Two more per-zone settings live in the same per-base_id entry, but
are NOT boolean METRICS toggles and have their own setters instead of
going through set_metric() - see image_library.py:
- max_saved_images: an integer cap, not a bool.
- thumbnail_passthrough_enabled: a bool, but a data-passthrough
  preference rather than a detection/comparison opt-in, so grouping it
  with METRICS (which set_metric()'s validation is specifically shaped
  around) would blur what "an independent opt-in metric" means here.
"""

METRICS = (
    "chlorosis", "necrosis", "spotting", "leaf_scorch", "powdery_mildew", "pest_indicators",
    "wilt_watch", "drama_level",
)

_DEFAULT_ENTRY = {
    "chlorosis": False,
    "necrosis": False,
    "spotting": False,
    "leaf_scorch": False,
    "powdery_mildew": False,
    "pest_indicators": False,
    "wilt_watch": False,
    "drama_level": False,
    # Set True the moment wilt_watch is switched on for a base that
    # has no reference image yet (see wilt_watch.py). Cleared once a
    # reference image is captured. Irrelevant while wilt_watch itself
    # is off, but left in place rather than removed, so a base that
    # re-enables wilt_watch without ever having captured a reference
    # is correctly flagged again without extra bookkeeping.
    "wilt_watch_config_necessary": False,
    # Per-zone cap on pinned/saved library images (image_library.py) -
    # 10 is a sane starting default (a small handful of "keepers" per
    # zone), not sourced from any storage-capacity calculation; tune
    # per-install once real image sizes/SD card capacity are known.
    "max_saved_images": 10,
    # Per-zone opt-in: pass a downsampled thumbnail of the Current
    # image to HA over MQTT (image_library.py, mqtt_presence.py) - off
    # by default, since it's optional data passthrough, not a required
    # part of the module's core function.
    "thumbnail_passthrough_enabled": False,
}

_MAX_SAVED_IMAGES_LIMIT = 100


class PerBaseSettingsManager:
    def __init__(self, config_module):
        self._config_module = config_module

    def get_settings(self, base_id):
        """
        Returns this base's current settings, general_health always
        included as a constant True (see module docstring - not
        stored, not toggleable).

        Merges onto _DEFAULT_ENTRY rather than only falling back to it
        when base_id is entirely absent - an existing-but-PARTIAL
        stored entry (e.g. one saved by an older version of this
        module, before a key like max_saved_images/
        thumbnail_passthrough_enabled existed) must still report every
        current key with a sane default, not KeyError or silently omit
        it. set_metric()/set_max_saved_images()/etc. already backfill
        missing keys into what they persist, but a base_id that's
        never been touched by any of those since upgrading otherwise
        wouldn't see this method's own fallback apply per-key - a real
        gap caught by test_per_base_settings.py's own "legacy entry"
        test.
        """
        cfg = self._config_module.load()
        entry = dict(_DEFAULT_ENTRY)
        entry.update(cfg.get("per_base_settings", {}).get(base_id, {}))
        result = {"general_health": True}
        result.update(entry)
        return result

    def set_metric(self, base_id, metric, enabled, has_reference=None):
        """
        Toggles one metric for one base. Raises ValueError for
        "general_health" (not toggleable - always on, see module
        docstring) or any unrecognized metric name, so a malformed
        request (portal or MQTT) fails loudly instead of silently
        creating a bogus config key.

        Turning wilt_watch on when this base has no stored reference
        image yet sets wilt_watch_config_necessary. has_reference, if
        given, is a callable(base_id) -> bool (in practice
        wilt_watch.WiltWatchManager.has_reference, injected since this
        module doesn't own wilt_watch's reference storage - see
        wilt_watch.py) so the flag isn't set redundantly for a base
        that's toggled wilt_watch off and back on but already has a
        reference from before. Callers with no way to check (has_
        reference left as None) get the simpler, always-set-on-enable
        behavior instead - re-prompting for a reference that already
        exists is a mild UX rough edge, never a correctness problem
        (capture_reference() just overwrites), so it's an acceptable
        default for a caller that doesn't have a WiltWatchManager handy.
        """
        if metric == "general_health":
            raise ValueError("general_health is always on and cannot be toggled")
        if metric not in METRICS:
            raise ValueError("unknown metric: {}".format(metric))

        cfg = self._config_module.load()
        per_base = cfg.setdefault("per_base_settings", {})
        entry = per_base.setdefault(base_id, dict(_DEFAULT_ENTRY))
        for key, default_value in _DEFAULT_ENTRY.items():
            entry.setdefault(key, default_value)

        was_enabled = entry[metric]
        entry[metric] = bool(enabled)

        if metric == "wilt_watch" and enabled and not was_enabled:
            if has_reference is None or not has_reference(base_id):
                entry["wilt_watch_config_necessary"] = True

        self._config_module.save(cfg)

    def clear_wilt_watch_config_necessary(self, base_id):
        """Called by wilt_watch.py after a reference image is successfully captured for base_id."""
        cfg = self._config_module.load()
        per_base = cfg.setdefault("per_base_settings", {})
        entry = per_base.setdefault(base_id, dict(_DEFAULT_ENTRY))
        entry["wilt_watch_config_necessary"] = False
        self._config_module.save(cfg)

    def set_max_saved_images(self, base_id, value):
        """
        Sets this base's own cap on pinned/saved library images
        (image_library.py). Bounded to a sane range (1-100) - zero
        would make "pin an image" always fail, and an unbounded value
        risks filling the Pi's SD card with nothing stopping it; both
        rejected with a clear ValueError rather than silently clamped,
        same "fail loudly on a malformed request" posture as
        set_metric().
        """
        if not isinstance(value, int) or isinstance(value, bool) or not (1 <= value <= _MAX_SAVED_IMAGES_LIMIT):
            raise ValueError("max_saved_images must be an integer between 1 and {}".format(_MAX_SAVED_IMAGES_LIMIT))

        cfg = self._config_module.load()
        per_base = cfg.setdefault("per_base_settings", {})
        entry = per_base.setdefault(base_id, dict(_DEFAULT_ENTRY))
        for key, default_value in _DEFAULT_ENTRY.items():
            entry.setdefault(key, default_value)
        entry["max_saved_images"] = value
        self._config_module.save(cfg)

    def set_thumbnail_passthrough_enabled(self, base_id, enabled):
        """Toggles this base's own MQTT thumbnail passthrough opt-in (item 10) - see image_library.py/mqtt_presence.py."""
        cfg = self._config_module.load()
        per_base = cfg.setdefault("per_base_settings", {})
        entry = per_base.setdefault(base_id, dict(_DEFAULT_ENTRY))
        for key, default_value in _DEFAULT_ENTRY.items():
            entry.setdefault(key, default_value)
        entry["thumbnail_passthrough_enabled"] = bool(enabled)
        self._config_module.save(cfg)
