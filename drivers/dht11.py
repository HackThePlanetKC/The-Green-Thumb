"""
drivers/dht11.py - DHT11 temperature/humidity sensor wrapper.

Wraps MicroPython's built-in `dht` module (bit-bang protocol handled
there already - not reimplemented here).

DHT11 datasheet requires >=1s between read attempts. The sensor_loop
in main.py already samples every 120s, so this isn't hit under normal
operation - the retry delay below only matters when a single read is
retried within one sampling cycle.
"""

import time

import dht
from machine import Pin


class DHT11Sensor:
    def __init__(self, pin_num, max_retries=3, retry_delay_s=1):
        self._sensor = dht.DHT11(Pin(pin_num))
        self._max_retries = max_retries
        self._retry_delay_s = retry_delay_s
        self.last_error = None

    def read(self):
        """
        Returns (temp_f, humidity_pct) as a tuple on success.
        Returns None if all retries failed - does not raise. A stuck or
        disconnected sensor should not crash the sensor loop; the caller
        (sensor_loop / health.py) decides how to handle a None reading
        (skip this cycle, count consecutive failures, etc).
        """
        self.last_error = None
        for attempt in range(self._max_retries):
            try:
                self._sensor.measure()
                temp_c = self._sensor.temperature()
                humidity_pct = self._sensor.humidity()
                return (_c_to_f(temp_c), humidity_pct)
            except OSError as e:
                self.last_error = e
                if attempt < self._max_retries - 1:
                    time.sleep(self._retry_delay_s)
        return None


def _c_to_f(temp_c):
    return temp_c * 9 / 5 + 32
