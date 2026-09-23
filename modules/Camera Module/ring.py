"""
ring.py - WS2812B RGBW ring driver (7 pixels), Pi/Linux side.

Uses rpi_ws281x directly - the reference C library's official Python
binding - not the CircuitPython/Blinka wrapper
(adafruit-circuitpython-neopixel). This module runs on plain
Raspbian/Linux, not CircuitPython, and the direct binding avoids an
extra abstraction layer this module has no other use for.

Data line: GPIO18 (Pi hardware PWM0), direct 3.3V drive, no level
shifter - see pins.py and this module's BUILD.md for the wiring and
the accepted-risk note on WS2812B's nominal ~4.5V "1"-bit threshold.

RGBW-specific: this ring has a real, physically separate white LED per
pixel, not an RGB-mixed approximation. The flash (see
flash_controller.py) uses ONLY the W channel - R/G/B are always driven
to 0 here. This driver deliberately has no method that sets R/G/B to
anything but 0; if a future use ever needs colored light from this
ring, that's a new method, not a repurposing of set_white().
"""

try:
    from rpi_ws281x import Color, PixelStrip
except ImportError:  # only available with rpi_ws281x installed (real Pi, or a dev venv with it pip-installed)
    PixelStrip = None
    Color = None

PIXEL_COUNT = 7
FREQ_HZ = 800000        # WS2812B data rate, effectively fixed by the part - not configurable per-install
DMA_CHANNEL = 10        # rpi_ws281x default; avoid changing unless it conflicts with another DMA user on this Pi
INVERT = False
PWM_CHANNEL = 0         # PWM channel 0 - matches GPIO18 (see pins.py); would need to be 1 if wired to GPIO13 instead


class Ring:
    """
    Thin wrapper around rpi_ws281x's PixelStrip, restricted to the one
    thing this module needs: driving every pixel's W channel to a
    single brightness level, R/G/B always 0. Not a general-purpose
    RGBW animation driver - see module docstring on why W-only.
    """

    def __init__(self, pixel_count=PIXEL_COUNT, pin=None, strip_cls=None):
        """
        strip_cls: injected for testability (tests pass a fake standing
        in for rpi_ws281x.PixelStrip rather than needing real hardware
        - see tests/test_ring.py). Defaults to the real PixelStrip.
        pin: defaults to pins.RING_DATA_PIN - imported lazily inside
        __init__, not at module load time, so importing ring.py alone
        (e.g. from a test that only exercises flash_controller.py with
        a fake Ring) never requires pins.py to be on the path either.
        """
        if pin is None:
            from pins import RING_DATA_PIN
            pin = RING_DATA_PIN

        strip_cls = strip_cls or PixelStrip
        if strip_cls is None:
            raise RuntimeError(
                "rpi_ws281x is not installed - this only runs with the real "
                "library present (pip install rpi_ws281x). See this module's BUILD.md."
            )

        self._strip = strip_cls(pixel_count, pin, FREQ_HZ, DMA_CHANNEL, INVERT, 255, PWM_CHANNEL)
        self._strip.begin()
        self._pixel_count = pixel_count

    def set_white(self, brightness):
        """
        Drives every pixel's W channel to `brightness` (0.0-1.0),
        clamped. R/G/B are always 0 - see module docstring.
        """
        level = max(0, min(255, round(brightness * 255)))
        color = Color(0, 0, 0, level)  # rpi_ws281x Color(red, green, blue, white)
        for i in range(self._pixel_count):
            self._strip.setPixelColor(i, color)
        self._strip.show()

    def off(self):
        self.set_white(0.0)
