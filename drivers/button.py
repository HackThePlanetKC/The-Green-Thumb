"""
drivers/button.py - Single pushbutton with short/long press discrimination.

Polled from an asyncio task (recommended ~20ms interval) rather than a
hardware IRQ - keeps debounce and timing logic in one place and avoids
MicroPython's IRQ-context restrictions (no allocation, limited operations
inside interrupt handlers).

Press duration bands:
  < short_min_ms                      -> ignored (bounce/accidental brush)
  short_min_ms .. short_max_ms        -> short press -> on_short_press,
                                          fires on release
  short_max_ms .. long_min_ms         -> dead zone, ignored (imprecise
                                          release shouldn't trigger either
                                          action)
  >= long_min_ms                      -> long press -> on_long_press,
                                          fires ONCE while still held, at
                                          the moment the threshold is
                                          crossed - not on release (BLE
                                          pairing mode starts immediately
                                          at 3s)

Re-entering WiFi setup mode is intentionally NOT a runtime press band
here - see wifi.check_setup_hold_at_boot(). A long runtime hold can
happen by accident (device pinned against something); a hold-during-
power-on cannot, so that's a boot-time-only check, separate from this
class entirely.
"""

import time

from machine import Pin


class Button:
    def __init__(
        self,
        pin_num,
        on_short_press=None,
        on_long_press=None,
        debounce_ms=50,
        short_min_ms=50,
        short_max_ms=1000,
        long_min_ms=3000,
        active_low=True,
    ):
        pull = Pin.PULL_UP if active_low else Pin.PULL_DOWN
        self._pin = Pin(pin_num, Pin.IN, pull)
        self._on_short_press = on_short_press
        self._on_long_press = on_long_press
        self._debounce_ms = debounce_ms
        self._short_min_ms = short_min_ms
        self._short_max_ms = short_max_ms
        self._long_min_ms = long_min_ms
        self._active_low = active_low

        self._pressed = False
        self._press_start_ms = 0
        self._long_fired = False
        self._last_change_ms = time.ticks_ms()
        self._raw_last = self._read_raw()

    def _read_raw(self):
        val = self._pin.value()
        return (val == 0) if self._active_low else (val == 1)

    def poll(self):
        """Call periodically (e.g. every 20ms) from an asyncio task."""
        now = time.ticks_ms()
        raw = self._read_raw()

        if raw != self._raw_last:
            # Signal just changed - (re)start the debounce window and
            # wait for it to settle before acting on it.
            self._raw_last = raw
            self._last_change_ms = now
            return

        if time.ticks_diff(now, self._last_change_ms) < self._debounce_ms:
            return  # not stable long enough yet

        if raw and not self._pressed:
            self._pressed = True
            self._press_start_ms = now
            self._long_fired = False

        elif raw and self._pressed:
            held_ms = time.ticks_diff(now, self._press_start_ms)
            if held_ms >= self._long_min_ms and not self._long_fired:
                self._long_fired = True
                if self._on_long_press:
                    self._on_long_press()

        elif not raw and self._pressed:
            held_ms = time.ticks_diff(now, self._press_start_ms)
            self._pressed = False
            if not self._long_fired and self._short_min_ms <= held_ms < self._short_max_ms:
                if self._on_short_press:
                    self._on_short_press()
            # Dead-zone releases and already-fired long presses are
            # intentionally ignored here - no callback on release for those.
