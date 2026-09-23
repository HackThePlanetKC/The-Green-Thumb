"""
test_flash_controller.py - stub/mock tests for flash_controller.maybe_flash().

No real hardware or rpi_ws281x install required: FakeRing stands in for
ring.Ring (duck-typed, see flash_controller.py), and a fake `sleep`
replaces time.sleep so exposure timing never actually blocks the test.

Run directly: python3 test_flash_controller.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flash_controller import maybe_flash  # noqa: E402

THRESHOLD = 50
BRIGHTNESS = 0.5
EXPOSURE_S = 0.2


class FakeRing:
    """Records every call instead of touching real WS2812 hardware."""

    def __init__(self):
        self.calls = []  # list of ("set_white", brightness) / ("off",)

    def set_white(self, brightness):
        self.calls.append(("set_white", brightness))

    def off(self):
        self.calls.append(("off",))


class FakeClock:
    """Records sleep() calls instead of actually blocking."""

    def __init__(self):
        self.slept_s = []

    def __call__(self, seconds):
        self.slept_s.append(seconds)


failures = []


def check(label, cond):
    status = "PASS" if cond else "FAIL"
    print("[{}] {}".format(status, label))
    if not cond:
        failures.append(label)


# --- Case 1: below threshold - flash fires, W-only, then off ---
ring = FakeRing()
clock = FakeClock()
fired = maybe_flash(30, THRESHOLD, EXPOSURE_S, ring, brightness=BRIGHTNESS, sleep=clock)

check("case1: returns True (flash fired)", fired is True)
check("case1: exactly 2 ring calls (on, then off)", len(ring.calls) == 2)
check("case1: first call is set_white with configured brightness", ring.calls[0] == ("set_white", BRIGHTNESS))
check("case1: second call is off", ring.calls[1] == ("off",))
check("case1: held for exactly exposure_s, once", clock.slept_s == [EXPOSURE_S])

# --- Case 2: at/above threshold - no flash ---
ring = FakeRing()
clock = FakeClock()
fired = maybe_flash(50, THRESHOLD, EXPOSURE_S, ring, brightness=BRIGHTNESS, sleep=clock)

check("case2: returns False (no flash, at threshold)", fired is False)
check("case2: ring never touched", ring.calls == [])
check("case2: never slept", clock.slept_s == [])

ring = FakeRing()
clock = FakeClock()
fired = maybe_flash(200, THRESHOLD, EXPOSURE_S, ring, brightness=BRIGHTNESS, sleep=clock)
check("case2b: returns False (well above threshold)", fired is False)
check("case2b: ring never touched", ring.calls == [])

# --- Case 3: an exception during exposure still turns the ring off (try/finally) ---
ring = FakeRing()


def raising_sleep(seconds):
    raise RuntimeError("simulated capture failure mid-exposure")


try:
    maybe_flash(10, THRESHOLD, EXPOSURE_S, ring, brightness=BRIGHTNESS, sleep=raising_sleep)
    raised = False
except RuntimeError:
    raised = True

check("case3: exception during exposure propagates (not swallowed)", raised is True)
check("case3: ring was still turned off despite the exception", ("off",) in ring.calls)
check("case3: ring was turned on before the (simulated) failure", ring.calls[0] == ("set_white", BRIGHTNESS))

# --- Case 4: RGB channels are never touched - set_white is the ONLY way this
# controller talks to the ring (a real Ring.set_white always forces R/G/B to
# 0 internally - see ring.py; this confirms the controller never calls
# anything else that could set a color) ---
ring = FakeRing()
maybe_flash(10, THRESHOLD, EXPOSURE_S, ring, brightness=BRIGHTNESS, sleep=FakeClock())
methods_called = {call[0] for call in ring.calls}
check("case4: only set_white/off called, nothing color-related", methods_called <= {"set_white", "off"})

print()
if failures:
    print("{} check(s) failed: {}".format(len(failures), failures))
    sys.exit(1)
else:
    print("All checks passed.")
