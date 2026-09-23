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
}


class PerBaseSettingsManager:
    def __init__(self, config_module):
        self._config_module = config_module

    def get_settings(self, base_id):
        """
        Returns this base's current settings, general_health always
        included as a constant True (see module docstring - not
        stored, not toggleable).
        """
        cfg = self._config_module.load()
        entry = cfg.get("per_base_settings", {}).get(base_id, _DEFAULT_ENTRY)
        result = {"general_health": True}
        result.update(entry)
        return result

    def set_metric(self, base_id, metric, enabled):
        """
        Toggles one metric for one base. Raises ValueError for
        "general_health" (not toggleable - always on, see module
        docstring) or any unrecognized metric name, so a malformed
        request (portal or MQTT) fails loudly instead of silently
        creating a bogus config key.

        Turning wilt_watch on when this base has no stored reference
        image yet sets wilt_watch_config_necessary - checked via
        has_reference (injected, since this module doesn't own
        wilt_watch's reference storage - see wilt_watch.py) so the
        flag isn't set redundantly for a base that already has one.
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
            entry["wilt_watch_config_necessary"] = True

        self._config_module.save(cfg)

    def clear_wilt_watch_config_necessary(self, base_id):
        """Called by wilt_watch.py after a reference image is successfully captured for base_id."""
        cfg = self._config_module.load()
        per_base = cfg.setdefault("per_base_settings", {})
        entry = per_base.setdefault(base_id, dict(_DEFAULT_ENTRY))
        entry["wilt_watch_config_necessary"] = False
        self._config_module.save(cfg)
