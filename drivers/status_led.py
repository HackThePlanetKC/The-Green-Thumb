"""
drivers/status_led.py - WS2812 (single pixel) status LED wrapper.

Boot animation plays automatically from construction until set_health()
is first called with real data - see _render_boot_animation. This is
the answer to "what does the LED show before any sensor data exists":
rather than inventing a 4th health color for "unknown", there's a
dedicated startup sequence: a brief warm-to-cool "sunrise" brightness
ramp (the actual boot animation, fixed 3s), followed by breathing green
(a general standby indicator - "still initializing, not ready yet" -
not just a boot-specific animation; it continues for as long as needed
until real health data arrives, however long that takes).

Priority: the sunrise is fully protected - nothing interrupts it. Once
breathing green begins, there's a further 5s grace window where it's
still protected. Only after that (sunrise + 5s = 8s total) can pairing
or WiFi-connecting preempt the still-ongoing breathing green - and if
those clear before set_health() is ever called, breathing green resumes
underneath them, since the device is still "booting" the whole time.
Night-mode suppression never applies to any of this, at any point.

Health status is shown as a solid color (green/yellow/red). If red
persists continuously for longer than `red_escalation_delay_s`, the LED
switches from solid to blinking red and an `on_escalation_change`
callback fires - main.py should wire this to publish a
"requires_immediate_attention" flag on the MQTT health topic.

Pairing mode (blinking blue) overrides normal health display entirely
while active, and always bypasses night-mode suppression - it's a
deliberate, immediate action the user just triggered by pressing the
button, so it should always be visible regardless of time of day.

Alert mode (set_alert) is for any unexpected condition that isn't
necessarily reflected in health.py's own status computation - e.g.
main.py's sensor-failure-duration tracking, which can fire even when
every reading currently in hand looks fine. Shown as a slow alternation
between the current health color and solid red, 5s per phase - a much
gentler cadence than the fast escalation blink, deliberately distinct
so the two don't read as the same signal. Takes priority over WiFi-
connecting but not pairing (an active, user-initiated action always
wins). Unlike escalated red, alert mode has NO override option for
night-mode suppression - it never overrides `led_off`, period. That's
a deliberate, permanent asymmetry with the escalation tier, not an
oversight: escalation is specifically about health getting worse in a
way that already went through the red-then-escalated pipeline, whereas
alert is a broader, catch-all mechanism that a future caller might use
for something far less urgent than "wake up right now" - defaulting to
always-respects-DND is the safer choice for a mechanism whose set of
callers isn't fully known yet.

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

_FIXED_COLORS = {
    "pairing": (0, 0, 255),
    "wifi_connecting": (128, 0, 128),
    "off": (0, 0, 0),
}

# Health-tier defaults - overridable per-tick via the health_colors
# argument to tick() (see StatusLed.__init__/tick docstrings and
# config.color_scheme in core/config.py). Kept separate from
# _FIXED_COLORS above since pairing/wifi_connecting/off are never
# affected by the colorblind/custom color scheme - only green/yellow/red are.
_DEFAULT_HEALTH_COLORS = {
    "green": (0, 255, 0),
    "yellow": (255, 180, 0),
    "red": (255, 0, 0),
}

# Boot animation reference colors - approximate blackbody swatches, not a
# precise CCT formula (WS2812 has no dedicated white channel anyway, so
# exact color-science accuracy isn't achievable or necessary here).
_BOOT_WARM_RGB = (255, 147, 41)    # ~2000K warm white approximation
_BOOT_COOL_RGB = (243, 242, 255)   # ~7000K cool white approximation
_BOOT_SUNRISE_MS = 3000            # phase 1 duration
_BOOT_GREEN_GRACE_MS = 5000        # additional grace after green starts before pairing/wifi can preempt
_BOOT_MAX_LEVEL = 0.30             # phase 1 brightness ramps 0 -> 30% of configured brightness

_ALERT_PHASE_MS = 5000             # 5s per phase - deliberately slow/gentle, see module docstring

_PULSE_DURATION_MS = 2000          # single breathing-style up/down cycle for "pulse_once" idle mode


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
        self._alert_active = False
        self._blink_on = False
        self._last_blink_toggle_ms = 0
        self._last_status_change_ms = None   # for "pulse_once" idle mode - see set_health/_render_idle_health
        self._health_colors = dict(_DEFAULT_HEALTH_COLORS)   # updated live from tick()'s health_colors argument

        # Boot animation: plays from construction until set_health() is
        # first called with real data. See module docstring.
        self._booting = True
        self._boot_start_ms = time.ticks_ms()

    def set_health(self, status):
        """
        status: "green", "yellow", or "red". Call whenever health.py
        recomputes overall status. The first call ends the boot
        animation, regardless of which status is passed - main.py should
        simply not call this until it has a real reading to report.

        Records the change timestamp unconditionally (cheap bookkeeping)
        regardless of the currently-configured idle_mode - tick() is the
        one that decides whether to actually render a "pulse_once" pulse
        from it, since idle_mode is passed live to tick() rather than
        known here. Keeps this method's signature unchanged for existing
        call sites.
        """
        self._booting = False
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
        self._last_status_change_ms = time.ticks_ms()

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

    def set_alert(self, active):
        """
        Generic alert flag - see module docstring for the full
        reasoning. Idempotent to call repeatedly with the same value
        (no per-call side effects to worry about, unlike set_health's
        red-timer tracking).
        """
        self._alert_active = active

    def tick(
        self, night_mode_active=False, led_off_in_night_mode=False, red_overrides_led_off=False,
        health_colors=None, idle_mode="solid", idle_breathe_period_ms=4000,
    ):
        """
        Call periodically (every 100-250ms recommended for smooth blink
        timing) from an asyncio task. All arguments are passed in from
        current config each call rather than cached, so a live
        set_config change takes effect immediately without needing to
        reconstruct this object.

        health_colors: optional {"green": (r,g,b), "yellow": (r,g,b),
        "red": (r,g,b)} - overrides the default health-tier colors (see
        config.color_scheme in core/config.py). None uses the built-in
        defaults. Only affects green/yellow/red - pairing/wifi_connecting/
        off are fixed regardless (see _FIXED_COLORS), since the
        colorblind/custom scheme is specifically about the health
        red-yellow-green semantics, not every color this driver shows.

        idle_mode/idle_breathe_period_ms: how plain (non-escalated,
        non-alert) health status is displayed - see _render_idle_health.

        Priority order: pairing (always visible, bypasses night mode) >
        alert (respects night mode, no override option - see module
        docstring) > WiFi connecting (breathing purple, respects night
        mode) > health display (respects night mode, with the
        escalated-red exception).
        """
        now = time.ticks_ms()
        self._health_colors = health_colors or _DEFAULT_HEALTH_COLORS

        if self._booting:
            elapsed = time.ticks_diff(now, self._boot_start_ms)
            protected = elapsed < (_BOOT_SUNRISE_MS + _BOOT_GREEN_GRACE_MS)
            if protected or not (self._pairing_active or self._wifi_connecting):
                self._render_boot_animation(now)
                return
            # Past the protected window AND pairing/wifi is active - fall
            # through to normal priority handling below, which renders
            # whichever of those is active. Booting itself is unaffected
            # (still True) - once pairing/wifi clear, later tick() calls
            # land back in the branch above and resume breathing green,
            # since the device is still "booting" until set_health() fires.

        if self._health_status == "red" and self._red_since_ms is not None and not self._escalated:
            elapsed_s = time.ticks_diff(now, self._red_since_ms) / 1000
            if elapsed_s >= self._red_escalation_delay_s:
                self._escalated = True
                if self._on_escalation_change:
                    self._on_escalation_change(True)

        if self._pairing_active:
            self._render_blink(_FIXED_COLORS["pairing"], now)
            return

        suppressed = night_mode_active and led_off_in_night_mode
        override_active = self._escalated and red_overrides_led_off

        if self._alert_active:
            # No override_active check here, deliberately - alert mode
            # never overrides night-mode DND, unlike escalated red.
            if suppressed:
                self._set_color(_FIXED_COLORS["off"])
            else:
                self._render_alert_flash(now)
            return

        if self._wifi_connecting:
            if suppressed and not override_active:
                self._set_color(_FIXED_COLORS["off"])
            else:
                self._render_breathing(_FIXED_COLORS["wifi_connecting"], now)
            return

        if suppressed and not override_active:
            self._set_color(_FIXED_COLORS["off"])
            return

        if self._health_status == "red" and self._escalated:
            self._render_blink(self._health_colors["red"], now)
        elif self._health_status in ("green", "yellow", "red"):
            self._render_idle_health(self._health_colors[self._health_status], now, idle_mode, idle_breathe_period_ms)
        else:
            self._set_color(_FIXED_COLORS["off"])

    def _render_idle_health(self, color, now, idle_mode, idle_breathe_period_ms):
        """
        Plain (non-escalated, non-alert) health display - four modes,
        see config.status_led.idle_mode in core/config.py:
        - "solid" (default): steady color, no motion.
        - "breathe": slow sine fade, see _render_breathing.
        - "pulse_once": off except for one breathing-style pulse right
          after a status change (see set_health's _last_status_change_ms
          bookkeeping), then back to off.
        - "off": LED stays dark for plain health status - pairing/wifi/
          alert still light up normally, since this only governs the
          lowest-priority tier.
        """
        if idle_mode == "off":
            self._set_color(_FIXED_COLORS["off"])
        elif idle_mode == "breathe":
            self._render_breathing(color, now, period_ms=idle_breathe_period_ms)
        elif idle_mode == "pulse_once":
            if self._last_status_change_ms is not None:
                elapsed = time.ticks_diff(now, self._last_status_change_ms)
                if elapsed < _PULSE_DURATION_MS:
                    phase = elapsed / _PULSE_DURATION_MS
                    level = math.sin(phase * math.pi)  # single 0->1->0 hump over the duration
                    scaled = tuple(int(c * self._brightness * level) for c in color)
                    self._np[0] = scaled
                    self._np.write()
                    return
            self._set_color(_FIXED_COLORS["off"])
        else:  # "solid"
            self._set_color(color)

    def _render_blink(self, color, now):
        elapsed = time.ticks_diff(now, self._last_blink_toggle_ms)
        if elapsed >= self._blink_interval_ms:
            self._blink_on = not self._blink_on
            self._last_blink_toggle_ms = now
        self._set_color(color if self._blink_on else _FIXED_COLORS["off"])

    def _render_alert_flash(self, now):
        """
        Alternates between the current health color and solid red,
        _ALERT_PHASE_MS per phase - a slow, deliberate cadence (default
        5s/phase, 10s full cycle), unlike _render_blink's fast on/off
        toggle, so the two read as visually distinct signals rather
        than the same urgency. Falls back to "off" as the non-red phase
        color if health_status is still None (e.g. an alert fires
        before the first real health reading ever arrives) rather than
        guessing a color that hasn't been reported yet. Uses
        self._health_colors["red"] (live, possibly colorblind/custom),
        not a fixed constant - an alert should look consistent with
        whatever health-color scheme is currently configured.
        """
        cycle_ms = _ALERT_PHASE_MS * 2
        showing_red = (now % cycle_ms) >= _ALERT_PHASE_MS
        base_color = self._health_colors.get(self._health_status, _FIXED_COLORS["off"])
        self._set_color(self._health_colors["red"] if showing_red else base_color)

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

    def _render_boot_animation(self, now):
        """
        Phase 1 (0-3s): linear brightness ramp 0->30% of configured
        brightness, color blends warm->cool white (see module docstring
        on why this is an approximation, not a precise CCT formula).
        Phase 2 (3s onward): breathing green - the general "still
        initializing" standby indicator, not just a boot-specific tail
        end. Continues indefinitely until set_health() is first called,
        however long that takes.
        """
        elapsed = time.ticks_diff(now, self._boot_start_ms)
        if elapsed < _BOOT_SUNRISE_MS:
            progress = max(0, min(1, elapsed / _BOOT_SUNRISE_MS))
            level = _BOOT_MAX_LEVEL * progress
            color = tuple(
                _BOOT_WARM_RGB[i] + (_BOOT_COOL_RGB[i] - _BOOT_WARM_RGB[i]) * progress
                for i in range(3)
            )
            scaled = tuple(int(c * self._brightness * level) for c in color)
            self._np[0] = scaled
            self._np.write()
        else:
            self._render_breathing(self._health_colors["green"], now)

    def _set_color(self, rgb):
        scaled = tuple(int(c * self._brightness) for c in rgb)
        self._np[0] = scaled
        self._np.write()
