"""
identity.py - Camera Module identity derived from the Pi's WiFi MAC.

Mirrors the base station's own core/identity.py in shape and purpose -
a stable, no-manual-config identifier derived from the WiFi
interface's MAC, same 6-hex-character derivation (last 3 MAC bytes,
uppercase). Adapted for Linux, which has no MicroPython
network.WLAN().mac() equivalent: reads the interface's MAC from
/sys/class/net/<iface>/address, the standard Linux sysfs location -
no root required, no extra dependency.

camera_id and the base station's base_id are visually consistent (same
shape) but never compared to each other or share a namespace - they
identify different kinds of devices in different topic trees.
"""

import os

_cached_id = None


def get_camera_id(iface="wlan0", sys_class_net="/sys/class/net"):
    """
    Returns a 6-character uppercase hex string derived from the last 3
    bytes of `iface`'s MAC address. Cached after first call - the MAC
    doesn't change at runtime.

    sys_class_net is overridable (points at a fake sysfs-shaped
    directory in tests) so this never touches the real filesystem in
    tests - see tests/test_identity.py.
    """
    global _cached_id
    if _cached_id is not None:
        return _cached_id

    path = os.path.join(sys_class_net, iface, "address")
    with open(path) as f:
        mac_str = f.read().strip()  # "aa:bb:cc:dd:ee:ff"

    octets = mac_str.split(":")
    last_three = octets[-3:]
    _cached_id = "".join(o.upper() for o in last_three)
    return _cached_id
