"""
core/health.py - Per-metric and overall plant health status calculation.

Each metric (temp_f, humidity_pct, light_fc, soil_moisture_pct) has a
green/red min-max range in config['thresholds']. Yellow is implicit:
outside the green range but not yet into the red range. A metric with
red_min/red_max set to None (currently light_fc - see
docs/ARCHITECTURE.md open items) can only ever be green or yellow, never
red, until real values are sourced.

Overall status is the worst of all evaluated metrics: red beats yellow
beats green. A metric with value=None (sensor unavailable or
uncalibrated) is skipped entirely - excluded from aggregation rather
than counted as green (a missing reading is not a good reading). If
every metric is None, overall status is None - this is a distinct,
rare runtime case (e.g. all sensors failing simultaneously), different
from "device just booted" (which drivers/status_led.py's boot animation
already handles without ever calling into this module). Callers should
decide their own fallback for a None overall status; this module does
not invent one.

This module does NOT decide when to publish or how the status LED
escalates - see core/mqtt_client.py (red-transition interrupt publish)
and drivers/status_led.py (sustained-red escalation to blinking, and
the boot animation). It only computes status.
"""

_SEVERITY_RANK = {"green": 0, "yellow": 1, "red": 2}

# Per-metric reason key prefixes, matching the naming used in the
# documented MQTT health payload example (docs/ARCHITECTURE.md), e.g.
# "soil_moisture_low". Severity (yellow vs red) is carried by the
# overall "status" field, not encoded into the reason string itself.
_METRIC_PREFIX = {
    "temp_f": "temp",
    "humidity_pct": "humidity",
    "light_fc": "light",
    "soil_moisture_pct": "soil_moisture",
}


def _evaluate_metric(value, thresholds):
    """
    Returns (status, direction) for a single metric, where status is
    "green", "yellow", "red", or None (value was None - unavailable).
    direction is "low"/"high"/None, combined with the metric's prefix by
    the caller (e.g. "low" + "soil_moisture" -> "soil_moisture_low").

    thresholds: dict with green_min, green_max, red_min, red_max - any
    of the four may be None (red_min/red_max None means that metric can
    never reach red on that side; green_min/green_max should always be
    set in practice, but None is handled the same way for safety).
    """
    if value is None:
        return (None, None)

    red_min = thresholds.get("red_min")
    red_max = thresholds.get("red_max")
    green_min = thresholds.get("green_min")
    green_max = thresholds.get("green_max")

    if red_min is not None and value < red_min:
        return ("red", "low")
    if red_max is not None and value > red_max:
        return ("red", "high")
    if green_min is not None and value < green_min:
        return ("yellow", "low")
    if green_max is not None and value > green_max:
        return ("yellow", "high")
    return ("green", None)


def evaluate_health(readings, thresholds_cfg):
    """
    readings: dict, any subset of {temp_f, humidity_pct, light_fc,
    soil_moisture_pct} -> numeric value or None.
    thresholds_cfg: config['thresholds'] (see core/config.py schema).

    Returns {"status": "green"|"yellow"|"red"|None, "reasons": [...]},
    matching the greenthumb/<base_id>/health MQTT payload shape (minus
    the requires_immediate_attention flag, which status_led.py's
    escalation callback adds separately - see docs/ARCHITECTURE.md).

    Metrics absent from `readings` are treated the same as value=None -
    skipped, no reason generated. status is None only if EVERY metric
    was skipped (see module docstring for what that means).
    """
    worst_status = None
    reasons = []

    for metric_key, prefix in _METRIC_PREFIX.items():
        if metric_key not in thresholds_cfg:
            continue  # threshold not configured for this metric at all
        value = readings.get(metric_key)
        status, direction = _evaluate_metric(value, thresholds_cfg[metric_key])

        if status is None:
            continue  # unavailable - excluded, not counted as green

        if status != "green":
            reasons.append("{}_{}".format(prefix, direction))

        if worst_status is None or _SEVERITY_RANK[status] > _SEVERITY_RANK[worst_status]:
            worst_status = status

    return {"status": worst_status, "reasons": reasons}
