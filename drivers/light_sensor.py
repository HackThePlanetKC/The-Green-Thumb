"""
drivers/light_sensor.py - Analog photoresistor (LDR) light sensor wrapper.

IMPORTANT: There is no universal ADC-to-foot-candle formula for a generic
photoresistor. Resistance-vs-light response is component-specific and
non-linear. This driver does NOT fabricate a conversion curve. Instead it
uses a two-point calibration (dark_raw, bright_raw against a known fc
value) - same pattern as the soil moisture driver.

Config ships with placeholder calibration values and calibrated=False.
The UI should prompt for real calibration during initial device setup.
read_fc() checks the calibrated flag explicitly rather than inferring
"not set up" from the placeholder numbers themselves, since a real
calibration could coincidentally resemble a placeholder.

Planned swap to BH1750 (digital I2C lux sensor) later: that sensor
outputs real lux directly, so its class only needs lux -> fc conversion
(lux / 10.764), no calibration curve at all. Any code using this driver
should call read_fc() and not care which implementation is behind it.
"""

import time

from machine import ADC, Pin

FC_PER_LUX = 1 / 10.764


class LDRLightSensor:
    def __init__(self, pin_num, samples=10, sample_delay_ms=200):
        self._adc = ADC(Pin(pin_num))
        self._adc.atten(ADC.ATTN_11DB)  # full 0-3.3V input range
        self._samples = samples
        self._sample_delay_ms = sample_delay_ms

    def read_raw_averaged(self):
        """Average of N raw ADC readings (0-65535 scale), sample_delay_ms apart."""
        total = 0
        for _ in range(self._samples):
            total += self._adc.read_u16()
            time.sleep_ms(self._sample_delay_ms)
        return total // self._samples

    def read_fc(self, raw_value, dark_raw, bright_raw, bright_fc, calibrated):
        """
        Converts a raw reading to foot-candles using a two-point linear
        calibration: dark_raw -> 0 fc, bright_raw -> bright_fc.

        Returns None unless `calibrated` is explicitly True - the
        placeholder config values are never trusted just because they're
        present. Also returns None if dark_raw == bright_raw (invalid,
        would divide by zero).
        """
        if not calibrated:
            return None
        if dark_raw is None or bright_raw is None or bright_fc is None:
            return None
        if dark_raw == bright_raw:
            return None
        fc = (raw_value - dark_raw) / (bright_raw - dark_raw) * bright_fc
        return max(0, fc)


def classify_light_level(raw_value, points):
    """
    Multi-point light level mode (config.light_mode.mode == "multi_point",
    an alternative to the continuous fc calibration above - see
    docs/ARCHITECTURE.md). Given the current raw reading and a list of
    user-defined named reference points (each {"label": str, "raw": int},
    added via the settings page by sampling a live reading while the
    sensor sits in that condition - e.g. "Direct Sun", "Bright Shade",
    "Low Light" are examples, not a fixed set; the user names and adds
    their own), returns the label of whichever point's raw value is
    closest to the current reading - simple nearest-neighbor
    classification, no interpolation.

    Returns None if points is empty (nothing configured to classify
    against) - never fabricates a label. Ties (equidistant from two
    points) resolve to whichever point appears first in the list - an
    arbitrary but deterministic and stable choice, not something a user
    needs to worry about in practice since exact ties on a live ADC
    reading are effectively never going to happen.

    Pure function, no I/O - deliberately doesn't care how raw_value or
    points were obtained, so it's usable both for live dashboard display
    and for testing without any hardware/config dependencies.
    """
    if not points:
        return None
    closest = min(points, key=lambda p: abs(p["raw"] - raw_value))
    return closest["label"]
