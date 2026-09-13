"""
core/wifi.py - WiFi STA connection with async retry, and an open AP
fallback for initial setup / re-configuration.

Written as async (not blocking time.sleep loops) because the whole
firmware runs as asyncio tasks - a blocking wait here would freeze the
display, BLE, and everything else during connection attempts.

STA mode: connects using config['wifi']['ssid']/['password']. On
failure, the caller should back off using next_backoff_s() rather than
retrying immediately. If the connection drops after having succeeded at
least once, keep calling connect_sta() in the background rather than
falling back to AP mode - sensors, display, and BLE all keep working
without WiFi; only MQTT/HA connectivity degrades until it reconnects.

AP (setup) mode: open network (no password), SSID
"GreenThumb-Setup-<base_id>". Entered automatically at boot if no WiFi
credentials are saved. Can also be re-entered by holding the button
down through power-on (see check_setup_hold_at_boot below) - this is a
boot-time-only check, not a runtime button press, so accidentally
holding the button during normal operation can never trigger it.
Serves web/server.py (not yet written) for entering WiFi credentials.
"""

import asyncio
import time

import network

import identity

AP_SSID_PREFIX = "GreenThumb-Setup-"
_MAX_BACKOFF_S = 60


def check_setup_hold_at_boot(pin_num, hold_s=3, active_low=True):
    """
    Blocking check performed exactly once at boot, BEFORE the asyncio
    event loop starts (called from main.py's startup sequence, not from
    within a task). If the button is already held down at power-on and
    stays held continuously for hold_s seconds, returns True (enter
    setup/AP mode instead of normal operation). Returns False immediately
    if the button isn't pressed, or as soon as it's released early.

    This is deliberately separate from drivers/button.py's runtime
    press-duration state machine: a hold-during-power-on can only happen
    if someone is intentionally holding the button as the device boots.
    A runtime long-press, by contrast, could happen by accident (e.g. the
    device pinned against something) - so re-entering setup mode is only
    reachable this way, not via any runtime press duration.
    """
    from machine import Pin

    pull = Pin.PULL_UP if active_low else Pin.PULL_DOWN
    pin = Pin(pin_num, Pin.IN, pull)

    def is_pressed():
        val = pin.value()
        return (val == 0) if active_low else (val == 1)

    if not is_pressed():
        return False

    start = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), start) < hold_s * 1000:
        if not is_pressed():
            return False
        time.sleep_ms(50)
    return True


class WifiManager:
    def __init__(self, ssid, password, connect_timeout_s=15):
        self._ssid = ssid
        self._password = password
        self._connect_timeout_s = connect_timeout_s
        self._sta = network.WLAN(network.STA_IF)
        self._ap = network.WLAN(network.AP_IF)
        self._backoff_s = 1
        self._ever_connected = False

    def has_credentials(self):
        return bool(self._ssid)

    async def connect_sta(self):
        """
        Attempts one STA connection attempt, awaiting up to
        connect_timeout_s without blocking other asyncio tasks. Returns
        True on success, False on timeout/failure. Does not retry
        internally or apply backoff itself - caller's connection task
        loop is responsible for calling next_backoff_s() and waiting
        between attempts.
        """
        if not self.has_credentials():
            return False

        self._sta.active(True)
        self._sta.connect(self._ssid, self._password)

        elapsed_ms = 0
        poll_interval_ms = 200
        timeout_ms = self._connect_timeout_s * 1000
        while not self._sta.isconnected():
            if elapsed_ms >= timeout_ms:
                return False
            await asyncio.sleep_ms(poll_interval_ms)
            elapsed_ms += poll_interval_ms

        self._ever_connected = True
        self._backoff_s = 1  # reset backoff after a real success
        return True

    def is_connected(self):
        return self._sta.isconnected()

    def ip_address(self):
        """Returns the current STA IP as a string, or None if not connected."""
        if not self._sta.isconnected():
            return None
        return self._sta.ifconfig()[0]

    def next_backoff_s(self):
        """
        Call after a failed connect_sta() to get how long to wait before
        trying again. Doubles each call up to _MAX_BACKOFF_S, so repeated
        failures (e.g. router down) don't hammer reconnect attempts.
        """
        current = self._backoff_s
        self._backoff_s = min(self._backoff_s * 2, _MAX_BACKOFF_S)
        return current

    def start_ap(self):
        """
        Starts the open setup AP. Does not disable STA - the ESP32
        supports concurrent AP+STA, so a background STA retry loop can
        keep running while setup mode is active.
        """
        base_id = identity.get_base_id()
        ssid = "{}{}".format(AP_SSID_PREFIX, base_id)
        self._ap.active(True)
        self._ap.config(essid=ssid, authmode=network.AUTH_OPEN)
        return ssid

    def stop_ap(self):
        self._ap.active(False)
