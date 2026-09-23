"""
test_ring.py - stub/mock tests for ring.Ring, confirming set_white()
only ever drives the W channel - R/G/B are always 0, on every pixel.

A fake PixelStrip/Color stand in for rpi_ws281x (not required to be
installed to run this test - see ring.Ring's strip_cls injection).

Run directly: python3 test_ring.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ring import Ring  # noqa: E402

PIXEL_COUNT = 7


def FakeColor(red, green, blue, white):
    """Mirrors rpi_ws281x.Color(r, g, b, w)'s signature/semantics closely
    enough for these tests - a plain tuple is enough to assert against."""
    return (red, green, blue, white)


class FakePixelStrip:
    """Stands in for rpi_ws281x.PixelStrip - records setPixelColor/show
    calls instead of driving real hardware."""

    def __init__(self, pixel_count, pin, freq_hz, dma, invert, brightness, channel):
        self.pixel_count = pixel_count
        self.pin = pin
        self.pixels = {}
        self.show_calls = 0
        self.began = False

    def begin(self):
        self.began = True

    def setPixelColor(self, index, color):
        self.pixels[index] = color

    def show(self):
        self.show_calls += 1


failures = []


def check(label, cond):
    status = "PASS" if cond else "FAIL"
    print("[{}] {}".format(status, label))
    if not cond:
        failures.append(label)


import ring as ring_module  # noqa: E402

ring_module.Color = FakeColor  # Ring.set_white() calls the module-level Color() directly

# --- Construction wires pin/strip correctly and calls begin() ---
strip = FakePixelStrip(PIXEL_COUNT, 18, 800000, 10, False, 255, 0)
r = Ring(pixel_count=PIXEL_COUNT, pin=18, strip_cls=lambda *a, **kw: strip)
check("construction calls strip.begin()", strip.began is True)

# --- set_white(1.0): every pixel gets W=255, R=G=B=0 ---
r.set_white(1.0)
check("set_white(1.0) touches every pixel", len(strip.pixels) == PIXEL_COUNT)
check(
    "set_white(1.0): every pixel is (0,0,0,255) - W only, full brightness",
    all(c == (0, 0, 0, 255) for c in strip.pixels.values()),
)
check("set_white(1.0) calls show() once", strip.show_calls == 1)

# --- set_white(0.5): W scales, R/G/B still 0 ---
strip.pixels.clear()
r.set_white(0.5)
check(
    "set_white(0.5): every pixel is (0,0,0,128) - W scaled, RGB still 0",
    all(c == (0, 0, 0, round(0.5 * 255)) for c in strip.pixels.values()),
)

# --- off() is exactly set_white(0.0): W=0, RGB=0 ---
strip.pixels.clear()
r.off()
check(
    "off(): every pixel is (0,0,0,0)",
    all(c == (0, 0, 0, 0) for c in strip.pixels.values()),
)

# --- Out-of-range brightness is clamped, never produces an invalid color ---
strip.pixels.clear()
r.set_white(1.5)
check("set_white(1.5) clamps to W=255, not overflowing/wrapping", all(c == (0, 0, 0, 255) for c in strip.pixels.values()))

strip.pixels.clear()
r.set_white(-0.5)
check("set_white(-0.5) clamps to W=0, not negative", all(c == (0, 0, 0, 0) for c in strip.pixels.values()))

# --- Missing rpi_ws281x raises a clear error rather than a confusing one ---
try:
    Ring(pixel_count=PIXEL_COUNT, pin=18, strip_cls=None)
    raised_missing_lib = False
except RuntimeError as e:
    raised_missing_lib = "rpi_ws281x" in str(e)
check("Ring() with no strip_cls and no rpi_ws281x installed raises a clear RuntimeError", raised_missing_lib)

print()
if failures:
    print("{} check(s) failed: {}".format(len(failures), failures))
    sys.exit(1)
else:
    print("All checks passed.")
