"""
wifi_manager.py - WiFi connection management via nmcli (NetworkManager),
Pi/Linux side.

API shape mirrors the base station's own core/wifi.py WifiManager
(has_credentials(), set_credentials(), connect_sta(), is_connected(),
ip_address(), next_backoff_s(), start_ap()/stop_ap()) for cross-project
consistency, even though the implementation is entirely different -
nmcli shells out to NetworkManager (Raspberry Pi OS's default network
stack since Bookworm) rather than driving a MicroPython network.WLAN()
object directly.

Deliberately fully synchronous, unlike the base's async WifiManager -
that module is async because MicroPython's whole main.py runs one
asyncio event loop and an async connect_sta() has to cooperate with
it. Nothing else in this Pi module runs an asyncio loop (mqtt_discovery
uses paho-mqtt's own background thread, web_portal uses the standard
library's synchronous http.server) - forcing this one method async
just for API-shape parity would mean bridging sync/async at every call
site for no functional benefit. See docs/ARCHITECTURE.md's modules/
layout note: match the implementation to the platform, not to the
base's own patterns where they don't translate.

Credentials are stored in plain JSON (see config.py), same as the base
station's own wifi.ssid/wifi.password - not a new gap introduced here,
the existing project-wide approach.

All actual nmcli invocations go through the injected `run` callable
(defaults to a real subprocess.run wrapper), so tests never shell out
to a real nmcli/NetworkManager - see tests/test_wifi_manager.py.
"""

import subprocess

AP_CONNECTION_NAME = "greenthumb-camera-setup"
AP_SSID_PREFIX = "GreenThumb-Camera-Setup-"

# NetworkManager's own default gateway for an nmcli-created hotspot
# (10.42.0.0/24) - DIFFERENT from the base station's 192.168.4.1
# (that's an ESP32/MicroPython AP-mode default, a fact about that
# platform, not a project-wide convention). Not configurable here -
# documented as a fixed fact of how nmcli's hotspot mode behaves.
AP_IP = "10.42.0.1"

_MAX_BACKOFF_S = 60


def _run(args, timeout_s=20):
    """
    The one place every nmcli call goes through - thin enough that
    tests substitute a fake instead of patching subprocess globally.
    Returns a subprocess.CompletedProcess; a timeout is surfaced as a
    non-zero-equivalent failure (returncode -1) rather than letting
    subprocess.TimeoutExpired propagate, so every caller only has to
    handle one failure shape (check returncode), not two.
    """
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args=args, returncode=-1, stdout="", stderr="timed out")


class WifiManager:
    def __init__(self, ssid, password, iface="wlan0", connect_timeout_s=15, run=_run):
        self._ssid = ssid
        self._password = password
        self._iface = iface
        self._connect_timeout_s = connect_timeout_s
        self._run = run
        self._backoff_s = 1

    def has_credentials(self):
        return bool(self._ssid)

    def set_credentials(self, ssid, password):
        """Updates credentials at runtime - used by the setup portal after saving, so it can immediately attempt connect_sta()."""
        self._ssid = ssid
        self._password = password

    def scan_networks(self):
        """
        Returns visible SSIDs (deduplicated, strongest-signal first)
        via `nmcli -t -f SSID,SIGNAL device wifi list`. Empty list on
        any nmcli failure (no WiFi hardware, insufficient permissions,
        etc.) - the setup page falls back to manual SSID entry rather
        than crashing the portal over a failed scan.
        """
        result = self._run(["nmcli", "-t", "-f", "SSID,SIGNAL", "device", "wifi", "list", "--rescan", "yes"])
        if result.returncode != 0:
            return []

        seen = {}
        for line in result.stdout.strip().splitlines():
            if ":" not in line:
                continue
            # nmcli -t is colon-separated; SIGNAL is a plain integer
            # and never contains a colon, so splitting on the LAST
            # colon correctly separates it even from an SSID that
            # itself contains one (nmcli backslash-escapes a literal
            # colon within a field - unescaped below).
            ssid, _, signal = line.rpartition(":")
            ssid = ssid.strip().replace("\\:", ":")
            if not ssid:
                continue  # hidden network - nothing to show/select
            try:
                signal_val = int(signal)
            except ValueError:
                signal_val = 0
            if ssid not in seen or signal_val > seen[ssid]:
                seen[ssid] = signal_val

        return [ssid for ssid, _ in sorted(seen.items(), key=lambda kv: kv[1], reverse=True)]

    def connect_sta(self):
        """
        Attempts one connection via `nmcli device wifi connect`,
        blocking up to connect_timeout_s (nmcli's own blocking-until-
        connected-or-failed behavior, same accepted-blocking-call
        tradeoff the base's own connect_sta()/ntp.sync_once() already
        document for their platform). Returns True on success, False
        on failure/timeout. Does not retry internally - see
        next_backoff_s() for the caller-owned retry/backoff pattern,
        mirroring the base's own division of responsibility.
        """
        if not self.has_credentials():
            return False

        result = self._run(
            ["nmcli", "device", "wifi", "connect", self._ssid, "password", self._password, "ifname", self._iface],
            self._connect_timeout_s,
        )
        if result.returncode == 0:
            self._backoff_s = 1  # reset backoff after a real success
            return True
        return False

    def is_connected(self):
        """
        True if `iface` currently has an active NetworkManager
        connection (state "connected"). Doesn't distinguish "connected
        to self._ssid specifically" from "connected to some other
        network" - callers that care should check ip_address()/scan
        results themselves; this only answers "does this interface
        have link right now."
        """
        result = self._run(["nmcli", "-t", "-f", "DEVICE,STATE", "device", "status"])
        if result.returncode != 0:
            return False
        for line in result.stdout.strip().splitlines():
            device, _, state = line.partition(":")
            if device == self._iface:
                return state == "connected"
        return False

    def ip_address(self):
        """Returns iface's current IPv4 address, or None if not connected."""
        result = self._run(["nmcli", "-t", "-f", "IP4.ADDRESS", "device", "show", self._iface])
        if result.returncode != 0:
            return None
        for line in result.stdout.strip().splitlines():
            _, _, value = line.partition(":")
            if value:
                return value.split("/")[0]  # nmcli reports "x.x.x.x/24" - strip the prefix length
        return None

    def next_backoff_s(self):
        """
        Call after a failed connect_sta() to get how long to wait
        before trying again. Doubles each call up to _MAX_BACKOFF_S.
        Not currently wired into any retry loop in this task's scope
        (main.py's setup flow is a one-shot attempt, retried only via
        the user resubmitting the portal form) - kept for API-shape
        parity and for a future long-running daemon mode to use.
        """
        current = self._backoff_s
        self._backoff_s = min(self._backoff_s * 2, _MAX_BACKOFF_S)
        return current

    def start_ap(self, camera_id):
        """
        Starts an open setup hotspot via nmcli's built-in hotspot mode.

        Unlike the base station's start_ap() (which keeps STA retrying
        concurrently - the ESP32 supports simultaneous AP+STA), a
        single Pi WiFi adapter generally cannot run AP and STA at the
        same time - starting the hotspot takes the interface offline
        from whatever network it might have had. That's fine for this
        module's use case: AP mode only ever runs during initial
        setup, before there's a WiFi connection to preserve anyway.
        """
        ssid = "{}{}".format(AP_SSID_PREFIX, camera_id)
        self._run([
            "nmcli", "device", "wifi", "hotspot",
            "ifname", self._iface, "con-name", AP_CONNECTION_NAME, "ssid", ssid,
        ])
        return ssid

    def stop_ap(self):
        self._run(["nmcli", "connection", "down", AP_CONNECTION_NAME])
