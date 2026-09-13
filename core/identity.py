"""
core/identity.py - Base station identity derived from the WiFi MAC address.

Used to build the MQTT base_id (see docs/ARCHITECTURE.md topic tree) and
the setup-mode AP SSID, so both stay consistent without any manual
configuration or flash storage of an identifier.
"""

import network

_cached_id = None


def get_base_id():
    """
    Returns a 6-character uppercase hex string derived from the last 3
    bytes of the STA interface's MAC address. Cached after first call -
    the MAC doesn't change at runtime. Activating the STA interface here
    (if not already active) is harmless and doesn't connect to anything -
    just required to read the MAC.
    """
    global _cached_id
    if _cached_id is not None:
        return _cached_id
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    mac = sta.mac()
    _cached_id = "".join("{:02X}".format(b) for b in mac[-3:])
    return _cached_id
