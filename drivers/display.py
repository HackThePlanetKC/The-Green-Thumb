"""
drivers/display.py - SSD1306 OLED wrapper: screen cycling + night mode.

Depends on the vendored `ssd1306` driver (install separately into /lib,
e.g. via `mip.install("ssd1306")` - not reproduced here, see project
notes) and on drivers/icons.py (bitmap icons, written alongside this
module). This module owns which screen is showing, auto-cycle timing,
manual-press handling, and the night-mode power schedule. Screen
*content* is supplied by the caller (main.py, or module_manager.py for
future BLE modules) as render functions that call draw_screen() - this
module has no knowledge of sensors, MQTT, or health status itself.

Night mode requires the device's local clock to already be NTP-synced
elsewhere (core/ntp.py) - `now_provider` is required at construction
rather than defaulting to time.localtime(), so an unsynced RTC can't
silently produce a wrong night-mode window.
"""

import time


class Display:
    def __init__(
        self,
        i2c,
        now_provider,
        width=128,
        height=64,
        auto_cycle_interval_s=10,
        resume_idle_s=20,
        night_mode_cfg=None,
    ):
        from ssd1306 import SSD1306_I2C  # vendored library, see module docstring

        self._oled = SSD1306_I2C(width, height, i2c)
        self._now_provider = now_provider  # callable -> (hour, minute)
        self._screens = []
        self._index = 0

        self._auto_cycle_interval_ms = auto_cycle_interval_s * 1000
        self._resume_idle_ms = resume_idle_s * 1000
        self._night_cfg = night_mode_cfg or {"enabled": False}

        self._last_manual_ms = 0
        self._last_auto_advance_ms = time.ticks_ms()
        self._paused = False       # true during the post-manual-press hold window
        self._screen_on = True

    def set_screens(self, screens):
        """
        Replaces the registered screen list and resets to the first screen.
        `screens` is a list of (screen_id, render_fn) tuples - use this for
        the fixed/core screen set (sensors, health, connection status) at
        startup. For screens that come and go at runtime (e.g. a BLE
        module pairing/unpairing), use add_screen/remove_screen instead so
        the whole list doesn't need to be rebuilt.
        """
        self._screens = list(screens)
        self._index = 0

    def add_screen(self, screen_id, render_fn):
        """
        Appends a screen at the end of the cycle. screen_id must be unique -
        intended for module_manager.py (not yet written) to call when a BLE
        module pairs, supplying a render_fn that knows how to display that
        module's specific data.

        render_fn should call self.draw_screen(icon_bytes, title, lines) -
        the standard format any screen (core or module) targets. Modules
        without their own icon should use icons.ICON_MODULE as a generic
        fallback. display.py has no built-in knowledge of any module's
        payload shape - the caller supplies a render function that already
        knows how to format that module type's state into a title + lines.

        No-ops if screen_id is already registered (avoids duplicate entries
        if called twice for the same module, e.g. on a reconnect).
        """
        if any(sid == screen_id for sid, _ in self._screens):
            return
        self._screens.append((screen_id, render_fn))

    def remove_screen(self, screen_id):
        """
        Removes a screen by id (e.g. when a module unpairs or goes
        unreachable). If the removed screen was at or before the current
        index, the index is adjusted so the display doesn't skip a screen
        or point past the end of the list.
        """
        for i, (sid, _) in enumerate(self._screens):
            if sid == screen_id:
                del self._screens[i]
                if self._screens and i <= self._index:
                    self._index = (self._index - 1) % len(self._screens)
                elif not self._screens:
                    self._index = 0
                return

    def manual_next(self):
        """
        Advance immediately and hold on this screen for resume_idle_s,
        even if night mode is active - called from button.py on a short
        press. If the display was off (night mode), this wakes it.
        """
        if not self._screens:
            return
        self._power_on()
        self._advance()
        self._last_manual_ms = time.ticks_ms()
        self._paused = True

    def tick(self):
        """
        Call periodically (e.g. every 1s) from an asyncio task. Handles,
        in order: the post-manual-press hold window, the night-mode
        power schedule, and normal auto-cycle advancement.
        """
        if not self._screens:
            return

        in_night = self._is_night_mode()

        if self._paused:
            elapsed = time.ticks_diff(time.ticks_ms(), self._last_manual_ms)
            if elapsed < self._resume_idle_ms:
                return  # still holding the manually-selected screen
            self._paused = False
            if in_night:
                self._power_off()
                return

        if in_night:
            if self._screen_on:
                self._power_off()
            return

        if not self._screen_on:
            self._power_on()

        elapsed = time.ticks_diff(time.ticks_ms(), self._last_auto_advance_ms)
        if elapsed >= self._auto_cycle_interval_ms:
            self._advance()
            self._last_auto_advance_ms = time.ticks_ms()

    def draw_lines(self, lines):
        """
        Clears the display and draws up to 8 lines of text (default font:
        8px line height fits 64px height; ~16 chars fit 128px width).
        Longer lines are truncated, not wrapped.
        """
        self._oled.fill(0)
        for i, line in enumerate(lines[:8]):
            self._oled.text(line[:16], 0, i * 8)
        self._oled.show()

    def draw_screen(self, icon_bytes, title, lines):
        """
        Standard screen layout: 16x16 icon top-left, a title beside it,
        then up to 5 additional text lines below. This is the format any
        screen render function should target - both the core sensor
        screens and any future BLE module's screen.

        icon_bytes: a 32-byte MONO_HLSB bitmap (see drivers/icons.py).
        Modules without a custom icon should pass icons.ICON_MODULE as a
        generic fallback rather than going without one.
        title: short string (~10 chars) shown beside the icon.
        lines: list of body text lines (~16 chars each), up to 5 shown.

        This exists so a future module's render_fn doesn't need to know
        anything about pixel positions - just supply an icon, a title,
        and lines of text, same as every core screen does.
        """
        import framebuf

        self._oled.fill(0)
        fb = framebuf.FrameBuffer(bytearray(icon_bytes), 16, 16, framebuf.MONO_HLSB)
        self._oled.blit(fb, 0, 0)
        self._oled.text(title[:10], 20, 4)
        y = 18
        for line in lines[:5]:
            self._oled.text(line[:16], 0, y)
            y += 8
        self._oled.show()

    def _advance(self):
        self._index = (self._index + 1) % len(self._screens)
        self._render_current()

    def _render_current(self):
        if self._screens:
            _, render_fn = self._screens[self._index]
            render_fn(self)

    def _power_on(self):
        if not self._screen_on:
            self._oled.poweron()
            self._screen_on = True
            self._render_current()

    def _power_off(self):
        if self._screen_on:
            self._oled.poweroff()
            self._screen_on = False

    def _is_night_mode(self):
        cfg = self._night_cfg
        if not cfg.get("enabled"):
            return False

        hour, minute = self._now_provider()
        now_min = hour * 60 + minute
        start_min = cfg["start_hour"] * 60 + cfg["start_minute"]
        end_min = cfg["end_hour"] * 60 + cfg["end_minute"]

        if start_min == end_min:
            # Zero-length window is ambiguous config, not "always night" -
            # treat as disabled rather than guess the intended meaning.
            return False
        if start_min < end_min:
            return start_min <= now_min < end_min
        else:
            # Window crosses midnight (e.g. 22:00 -> 07:00).
            return now_min >= start_min or now_min < end_min
