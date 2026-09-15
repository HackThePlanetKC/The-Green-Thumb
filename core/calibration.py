"""
core/calibration.py - Soil moisture and light sensor calibration state
machine, driven by MQTT commands (calibration/command topic) or the
initial-setup web portal issuing the same internal commands (see
docs/ARCHITECTURE.md - one state machine, two entry points, not two
separate code paths).

Flow (same shape for both targets, different step names/data):
  soil:  start -> read_dry  -> read_wet    -> save (dry_raw, wet_raw)
  light: start -> read_dark -> read_bright -> save (dark_raw, bright_raw,
                                                      bright_fc - a user-
                                                      supplied reference
                                                      value, since the
                                                      device has no way
                                                      to know the actual
                                                      fc level it's being
                                                      held up against)
  cancel: valid at any point, reverts to idle without touching config.

Each read step averages 10 raw ADC samples over ~2s - this module calls
drivers/soil_moisture.py's or drivers/light_sensor.py's
read_raw_averaged(), it does not implement its own sampling.

On save, the two raw readings must differ by at least min_delta_raw (a
per-target placeholder value - see config.py comments on both
soil_calibration and light_calibration) or the save is rejected: config
is left untouched and the state reverts to idle with an error recorded
in status(). This module owns state transitions and config writes; it
does not own MQTT transport (mqtt_client.py calls into this via the
on_calibration_command callback) or publish anything itself - the
caller (main.py) is expected to call publish via mqtt_client after
every transition so the retained calibration/status topic reflects
current state immediately.
"""

import config as config_module

_SOIL_STEPS = {"first": "read_dry", "second": "read_wet"}
_LIGHT_STEPS = {"first": "read_dark", "second": "read_bright"}
_VALID_TARGETS = ("soil", "light")


class CalibrationManager:
    def __init__(self, cfg, soil_sensor, light_sensor):
        """
        cfg: live config dict (mutated in place on save, then persisted
        via config.save() - same pattern as mqtt_client.py's set_config,
        so every other task sharing this dict sees the change immediately).
        soil_sensor / light_sensor: driver instances exposing
        read_raw_averaged() (see drivers/soil_moisture.py,
        drivers/light_sensor.py).
        """
        self._cfg = cfg
        self._sensors = {"soil": soil_sensor, "light": light_sensor}
        self._state = "idle"
        self._target = None
        self._first_raw = None
        self._second_raw = None
        self._last_error = None

    def status(self):
        """
        Current state as a dict, matching the calibration/status MQTT
        payload shape (see docs/ARCHITECTURE.md). "error" is non-null
        only immediately after a rejected save - the next start/cancel
        clears it.
        """
        return {
            "state": self._state,
            "target": self._target,
            "first_raw": self._first_raw,
            "second_raw": self._second_raw,
            "error": self._last_error,
        }

    def handle_command(self, payload):
        """
        payload: dict from the calibration/command MQTT topic (or an
        equivalent internal call from the web portal). Returns True if
        the command was accepted, False if rejected (invalid target,
        wrong state for that action, etc). There's no MQTT ack channel
        (see docs/ARCHITECTURE.md) - callers should check status() for
        the resulting state/error rather than relying on this return
        value alone for user feedback.
        """
        action = payload.get("action")

        if action == "start":
            return self._handle_start(payload)
        if action == "cancel":
            return self._handle_cancel()
        if action in ("read_dry", "read_dark"):
            return self._handle_first_read(action)
        if action in ("read_wet", "read_bright"):
            return self._handle_second_read(action)
        if action == "save":
            return self._handle_save(payload)
        return False

    def _handle_start(self, payload):
        target = payload.get("target")
        if target not in _VALID_TARGETS:
            return False
        self._state = "awaiting_first_read"
        self._target = target
        self._first_raw = None
        self._second_raw = None
        self._last_error = None
        return True

    def _handle_cancel(self):
        self._state = "idle"
        self._target = None
        self._first_raw = None
        self._second_raw = None
        self._last_error = None
        return True

    def _expected_first_action(self):
        return _SOIL_STEPS["first"] if self._target == "soil" else _LIGHT_STEPS["first"]

    def _expected_second_action(self):
        return _SOIL_STEPS["second"] if self._target == "soil" else _LIGHT_STEPS["second"]

    def _handle_first_read(self, action):
        if self._state != "awaiting_first_read":
            return False
        if action != self._expected_first_action():
            return False  # e.g. "read_dark" sent while target is "soil"
        self._first_raw = self._sensors[self._target].read_raw_averaged()
        self._state = "awaiting_second_read"
        return True

    def _handle_second_read(self, action):
        if self._state != "awaiting_second_read":
            return False
        if action != self._expected_second_action():
            return False
        self._second_raw = self._sensors[self._target].read_raw_averaged()
        self._state = "awaiting_save"
        return True

    def _handle_save(self, payload):
        if self._state != "awaiting_save":
            return False
        if self._target == "soil":
            return self._save_soil()
        return self._save_light(payload)

    def _save_soil(self):
        min_delta = self._cfg["soil_calibration"].get("min_delta_raw", 0)
        if abs(self._first_raw - self._second_raw) < min_delta:
            self._last_error = "insufficient_delta"
            self._state = "idle"
            # target is deliberately NOT cleared here (unlike _handle_cancel) -
            # status() still needs to report which target this error belongs
            # to, so the web UI can display it on the right section (see
            # web/static/calibration.html's renderSection, which checks
            # status.target === target before showing status.error - an
            # earlier version cleared target here, making that check always
            # false and silently swallowing the error message entirely).
            # The next start() overwrites target regardless, so leaving it
            # set here doesn't cause any lingering-state problem.
            return False

        self._cfg["soil_calibration"]["dry_raw"] = self._first_raw
        self._cfg["soil_calibration"]["wet_raw"] = self._second_raw
        config_module.save(self._cfg)
        self._handle_cancel()
        return True

    def _save_light(self, payload):
        bright_fc = payload.get("bright_fc")
        if bright_fc is None:
            self._last_error = "missing_bright_fc"
            return False  # stay in awaiting_save - just needs the missing field, not a full restart

        min_delta = self._cfg["light_calibration"].get("min_delta_raw", 0)
        if abs(self._first_raw - self._second_raw) < min_delta:
            self._last_error = "insufficient_delta"
            self._state = "idle"
            # See _save_soil's comment - target intentionally not cleared.
            return False

        self._cfg["light_calibration"]["dark_raw"] = self._first_raw
        self._cfg["light_calibration"]["bright_raw"] = self._second_raw
        self._cfg["light_calibration"]["bright_fc"] = bright_fc
        self._cfg["light_calibration"]["calibrated"] = True
        config_module.save(self._cfg)
        self._handle_cancel()
        return True
