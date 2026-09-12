"""
drivers/soil_moisture.py - Analog capacitive soil moisture sensor wrapper.

Uses machine.ADC.read_u16() (portable 0-65535 scale across MicroPython
ports) rather than a raw 12-bit ADC count, so calibration values stored
in config.py aren't tied to this specific chip's ADC resolution.

Moisture % is computed via direct linear interpolation between the two
calibrated raw readings (dry_raw -> 0%, wet_raw -> 100%). This works
regardless of whether the sensor's raw value rises or falls as it gets
wetter - polarity is never assumed, only the two labeled endpoints from
calibration.
"""

import time

from machine import ADC, Pin


class SoilMoistureSensor:
    def __init__(self, pin_num, samples=10, sample_delay_ms=200):
        self._adc = ADC(Pin(pin_num))
        self._adc.atten(ADC.ATTN_11DB)  # full 0-3.3V input range
        self._samples = samples
        self._sample_delay_ms = sample_delay_ms

    def read_raw_averaged(self):
        """
        Returns the average of N raw ADC readings (0-65535 scale), taken
        sample_delay_ms apart. Same method is used for normal operation
        and for the calibration flow's dry/wet reads, so calibration and
        runtime readings are taken the same way.

        Default of 10 samples / 200ms spacing matches the calibration
        sampling spec (~2s per read) decided earlier - kept as the shared
        default here rather than duplicated as a separate constant.
        """
        total = 0
        for _ in range(self._samples):
            total += self._adc.read_u16()
            time.sleep_ms(self._sample_delay_ms)
        return total // self._samples

    def read_percent(self, raw_value, dry_raw, wet_raw):
        """
        Converts a raw reading to moisture % using calibration endpoints.

        Returns None if calibration hasn't been done yet (dry_raw or
        wet_raw is None) or is invalid (dry_raw == wet_raw, which would
        divide by zero). Caller must treat None as "soil moisture
        unavailable, needs calibration" - this never fabricates a
        fallback percentage.
        """
        if dry_raw is None or wet_raw is None:
            return None
        if dry_raw == wet_raw:
            return None
        percent = (raw_value - dry_raw) / (wet_raw - dry_raw) * 100
        return max(0, min(100, percent))
