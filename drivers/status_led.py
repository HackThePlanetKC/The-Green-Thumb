"""
drivers/status_led.py - WS2812 (single pixel) status LED wrapper.

Health status is shown as a solid color (green/yellow/red). If red
persists continuously for longer than `red_escalation_delay_s`, the LED
switches from solid to blinking red and an `on_escalation_change`
callback fires - main.py should wire this to publish a
"requires_immediate_attention" flag on the MQTT health topic.

Pairing mode (blinking blue) overrides normal health display entirely
while active, and always bypasses night-mode suppression - it's a
deliberate, immediate action the user just triggered by pressing the
button, so it should always be visible regardless of time of day.

WiFi connecting is shown as breathing (smooth sine-fade) purple. Unlike
pairing, this is NOT exempt from night-mode suppression - a WiFi drop
and reconnect can happen unattended at any hour (e.g. a router reboot
at 3am), and there's no reason for the device to light up in a dark
room over something the user didn't initiate.

Night mode can suppress the LED (`led_off`). By design, this suppression
does NOT get overridden by plain solid red - only by the escalated
(blinking) tier, and only if `red_overrides_led_off` is enabled. This
matches the two-tier urgency model: solid red is "needs attention",
blinking red is "needs attention now", and only the latter is treated as
urgent enough to matter during night mode.

Brightness is a 0.0-1.0 scalar applied to all colors. The default
(0.15) is a placeholder - the enclosure diffuses this LED under a thin
printed section (under the fist model's thumbnail), and the right
brightness for that can only be determined once the physical part
exists. Expect to retune this.
"""

import math
import time

from machine import Pin
from neopixel import NeoPixel

COLORS = {
    "green": (0, 255, 0),
    "yellow": (255, 180, 0),
    "red": (255, 0, 0),
    "pairing": (0, 0, 255),
    "wifi_connecting": (128, 0, 128),
    "off": (0, 0, 0),
}


class StatusLed:
    def __init__(
        self,
        pin_num,
        brightness=0.15,
        blink_interval_ms=500,
        red_escalation_delay_s=3600,
        on_escalation_change=None,
    ):
        self._np = NeoPixel(Pin(pin_num), 1)
        self._brightness = brightness
        self._blink_interval_ms = blink_interval_ms
        self._red_escalation_delay_s = red_escalation_delay_s
        self._on_escalation_change = on_escalation_change  # callback(bool)

        self._health_status = None     # "green" | "yellow" | "red" | None
        self._red_since_ms = None
        self._escalated = False
        self._pairing_active = False
        self._wifi_connecting = False
        self._blink_on = False
        self._last_blink_toggle_ms = 0

    def set_health(self, status):
        """
        status: "green", "yellow", or "red". Call whenever health.py
        recomputes overall status. Tracks how long red has been
        continuously active so tick() can compute escalation - a status
        change away from red resets the timer and de-escalates.
        """
        if status == self._health_status:
            return
        if status == "red":
            self._red_since_ms = time.ticks_ms()
        else:
            self._red_since_ms = None
            if self._escalated:
                self._escalated = False
                if self._on_escalation_change:
                    self._on_escalation_change(False)
        self._health_status = status

    def set_pairing_mode(self, active):
        """Pairing mode overrides normal health display with blinking blue."""
        self._pairing_active = active

    def set_wifi_connecting(self, active):
        """
        WiFi connecting/reconnecting shows breathing purple. Unlike
        pairing mode, this respects night-mode suppression (see module
        docstring) - it's not a user-initiated action.
        """
        self._wifi_connecting = active

    def tick(self, night_mode_active=False, led_off_in_night_mode=False, red_overrides_led_off=False):
        """
        Call periodically (every 100-250ms recommended for smooth blink
        timing) from an asyncio task. night_mode_active/led_off_in_night_mode/
        red_overrides_led_off are passed in from current config each call
        rather than cached, so a live set_config change takes effect
        immediately without needing to reconstruct this object.

        Priority order: pairing (always visible, bypasses night mode) >
        WiFi connecting (breathing purple, respects night mode) > health
        display (respects night mode, with the escalated-red exception).
        """
        now = time.ticks_ms()

        if self._health_status == "red" and self._red_since_ms is not None and not self._escalated:
            elapsed_s = time.ticks_diff(now, self._red_since_ms) / 1000
            if elapsed_s >= self._red_escalation_delay_s:
                self._escalated = True
                if self._on_escalation_change:
                    self._on_escalation_change(True)

        if self._pairing_active:
            self._render_blink(COLORS["pairing"], now)
            return

        suppressed = night_mode_active and led_off_in_night_mode
        override_active = self._escalated and red_overrides_led_off

        if self._wifi_connecting:
            if suppressed:
                self._set_color(COLORS["off"])
            else:
                self._render_breathing(COLORS["wifi_connecting"], now)
            return

        if suppressed and not override_active:
            self._set_color(COLORS["off"])
            return

        if self._health_status == "red" and self._escalated:
            self._render_blink(COLORS["red"], now)
        elif self._health_status in ("green", "yellow", "red"):
            self._set_color(COLORS[self._health_status])
        else:
            self._set_color(COLORS["off"])

    def _render_blink(self, color, now):
        elapsed = time.ticks_diff(now, self._last_blink_toggle_ms)
        if elapsed >= self._blink_interval_ms:
            self._blink_on = not self._blink_on
            self._last_blink_toggle_ms = now
        self._set_color(color if self._blink_on else COLORS["off"])

    def _render_breathing(self, color, now, period_ms=3000):
        """
        Smooth sine-based brightness ramp (0 -> full -> 0 over period_ms),
        distinct from the hard on/off of _render_blink - reads as a slow
        "breathing" pulse rather than a blink.
        """
        phase = (now % period_ms) / period_ms
        level = (math.sin(phase * 2 * math.pi - math.pi / 2) + 1) / 2  # 0..1
        scaled = tuple(int(c * self._brightness * level) for c in color)
        self._np[0] = scaled
        self._np.write()

    def _set_color(self, rgb):
        scaled = tuple(int(c * self._brightness) for c in rgb)
        self._np[0] = scaled
        self._np.write()
