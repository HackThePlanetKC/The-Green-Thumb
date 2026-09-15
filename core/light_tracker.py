"""
core/light_tracker.py - Daily light-duration tracking: cumulative hours
of "usable" light and cumulative hours of high-intensity light, reset at
local midnight.

Two independent thresholds (see docs/ARCHITECTURE.md and core/config.py):
- light_tracking.present_threshold_fc: minimum fc to count as "getting
  light" at all (default 75fc - below this counts as effectively dark
  for photoperiod purposes).
- thresholds.light_fc.red_max: minimum fc to count as "high intensity"
  (sunburn risk / direct light). Currently unset (None) in default
  config - see docs/ARCHITECTURE.md open items. While unset,
  high_intensity_minutes simply never increments (the check never
  triggers), and the optional high_intensity_hours_target (below) is
  never evaluated - left as None/None rather than misreporting "always
  failing" for a metric that isn't measurable yet.

Sampling: call record_sample() once per local sensor sample (every
config['timing']['sample_interval_s'], 2 min default) with the current
light_fc reading. This module has no timer of its own - it trusts the
caller's sample cadence to convert samples to elapsed minutes
consistently (sample_interval_s is passed in once at construction).

Midnight rollover: call check_rollover() with the current local date
(e.g. from ntp.local_date()) on the same cadence. When the date changes
from what this module last saw, the completed day's totals are
evaluated and returned as a summary dict (for the caller to publish via
mqtt_client.publish_light_summary), then counters reset for the new day.

Status logic for the daily summary mirrors how core/health.py treats an
unsourced red threshold: light_hours_target only has a green range
(min/max), no separate red tier was ever specified, so the only
"non-green" outcome is yellow - "below_target_hours" or
"above_target_hours". This is a deliberate consistency choice, not a
gap: adding a fabricated red tier for light-hours when none was
specified would be the same mistake as guessing a red_max for light_fc.

high_intensity_hours_target (optional, non-essential - e.g. "needs at
least 4h of direct sun daily") is evaluated the same way, but as a
SEPARATE status/reason pair in the summary
(high_intensity_status/high_intensity_reason), not folded into the
main status/reason. It never escalates or overrides the primary
light-hours status - it's informational, gated on both a target being
configured AND thresholds.light_fc.red_max being set (see above).
Per-category light-level duration tracking (e.g. minutes spent at each
of several user-defined brightness levels - see
drivers/light_sensor.py's classify_light_level()) is explicitly NOT
implemented here - flagged as a future Home Assistant integration
roadmap item instead (see README.md), since HA has far more room for
that kind of historical/statistical tracking than this device does.
"""


class LightTracker:
    def __init__(self, sample_interval_s):
        self._sample_interval_s = sample_interval_s
        self._light_minutes_today = 0.0
        self._high_intensity_minutes_today = 0.0
        self._current_date = None  # (year, month, day), set on first call to check_rollover

    def record_sample(self, light_fc, thresholds_cfg, light_tracking_cfg):
        """
        Call once per local sensor sample. light_fc=None (sensor
        unavailable/uncalibrated) contributes nothing to either counter
        for this sample - consistent with core/health.py's treatment of
        missing readings (excluded, not counted as "no light").
        """
        if light_fc is None:
            return

        sample_minutes = self._sample_interval_s / 60

        present_threshold = light_tracking_cfg.get("present_threshold_fc")
        if present_threshold is not None and light_fc >= present_threshold:
            self._light_minutes_today += sample_minutes

        red_max = thresholds_cfg.get("light_fc", {}).get("red_max")
        if red_max is not None and light_fc >= red_max:
            self._high_intensity_minutes_today += sample_minutes

    def get_light_hours_today(self):
        """
        Returns the current running total of light_minutes_today, in
        hours, as of right now - NOT waiting for midnight rollover. Used
        by the dashboard's "light so far today" display (see
        web/server.py), which is deliberately static-on-load rather
        than part of the live 5s-polled readings - this getter exists
        specifically to be called on demand (page load, manual refresh
        button) rather than continuously.
        """
        return self._light_minutes_today / 60

    def check_rollover(self, current_local_date, thresholds_cfg, light_tracking_cfg):
        """
        current_local_date: (year, month, day) tuple.
        thresholds_cfg: config['thresholds'] - needed now to check
        whether thresholds.light_fc.red_max is configured, which gates
        whether the optional high-intensity-hours target (see
        _build_summary) can be evaluated at all.

        Returns a summary dict for the day that just ended, and resets
        counters, if current_local_date differs from the last-seen date.
        Returns None otherwise - including on the very first call ever,
        which just records the starting date without evaluating
        anything (there's no "completed day" yet to summarize).
        """
        if self._current_date is None:
            self._current_date = current_local_date
            return None

        if current_local_date == self._current_date:
            return None

        summary = self._build_summary(self._current_date, thresholds_cfg, light_tracking_cfg)
        self._current_date = current_local_date
        self._light_minutes_today = 0.0
        self._high_intensity_minutes_today = 0.0
        return summary

    def _build_summary(self, date_tuple, thresholds_cfg, light_tracking_cfg):
        light_hours = self._light_minutes_today / 60
        high_intensity_hours = self._high_intensity_minutes_today / 60

        target = light_tracking_cfg.get("hours_target", {})
        target_min = target.get("min")
        target_max = target.get("max")

        status = "green"
        reason = None
        if target_min is not None and light_hours < target_min:
            status = "yellow"
            reason = "below_target_hours"
        elif target_max is not None and light_hours > target_max:
            status = "yellow"
            reason = "above_target_hours"

        # Optional, non-essential: high-intensity/direct-light daily
        # target (e.g. "needs at least 4h of direct sun"). Only
        # evaluated if BOTH a target (min or max) is configured AND
        # thresholds.light_fc.red_max is set - without red_max,
        # high_intensity_minutes_today can never increment above zero
        # (see record_sample), so evaluating against a target would
        # misreport "always failing" for a metric that isn't actually
        # measurable yet, not a real problem with the plant. Left as
        # None/None (not evaluated) rather than a misleading status.
        hi_status = None
        hi_reason = None
        hi_target = light_tracking_cfg.get("high_intensity_hours_target", {})
        hi_min = hi_target.get("min")
        hi_max = hi_target.get("max")
        red_max_configured = thresholds_cfg.get("light_fc", {}).get("red_max") is not None

        if red_max_configured and (hi_min is not None or hi_max is not None):
            hi_status = "green"
            if hi_min is not None and high_intensity_hours < hi_min:
                hi_status = "yellow"
                hi_reason = "below_target_hours"
            elif hi_max is not None and high_intensity_hours > hi_max:
                hi_status = "yellow"
                hi_reason = "above_target_hours"

        return {
            "date": "{:04d}-{:02d}-{:02d}".format(*date_tuple),
            "light_hours": round(light_hours, 2),
            "high_intensity_hours": round(high_intensity_hours, 2),
            "status": status,
            "reason": reason,
            "high_intensity_status": hi_status,
            "high_intensity_reason": hi_reason,
        }
