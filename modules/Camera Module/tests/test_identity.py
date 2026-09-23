"""test_identity.py - stub-based tests for identity.get_camera_id(). Run: python3 test_identity.py"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import identity  # noqa: E402

failures = []


def check(label, cond):
    status = "PASS" if cond else "FAIL"
    print("[{}] {}".format(status, label))
    if not cond:
        failures.append(label)


identity._cached_id = None  # reset module-level cache between test runs

with tempfile.TemporaryDirectory() as d:
    iface_dir = os.path.join(d, "wlan0")
    os.makedirs(iface_dir)
    with open(os.path.join(iface_dir, "address"), "w") as f:
        f.write("de:ad:be:ef:a1:b2\n")

    camera_id = identity.get_camera_id(iface="wlan0", sys_class_net=d)
    check("derives last 3 MAC bytes, uppercase, no colons", camera_id == "EFA1B2")

    # caching: even pointed at a different (nonexistent) path, the
    # second call should return the cached value without re-reading
    cached = identity.get_camera_id(iface="wlan0", sys_class_net="/does/not/exist")
    check("second call returns the cached value, doesn't re-read", cached == "EFA1B2")

print()
if failures:
    print("{} check(s) failed: {}".format(len(failures), failures))
    sys.exit(1)
else:
    print("All checks passed.")
