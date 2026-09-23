"""
pins.py - GPIO pin assignments for the Camera Module (Pi Zero 2W).

BCM numbering, matching rpi_ws281x's own pin argument convention (and
the numbering printed on most Pi GPIO header references) - not
physical header position.

Separate from the base station's own /pins.py (ESP32-C3, MicroPython) -
this module runs on entirely different hardware/OS, so pin numbers
here have no relationship to that file at all. See docs/ARCHITECTURE.md's
modules/ layout note.
"""

# WS2812B RGBW ring data line. GPIO18 is Pi hardware PWM0 - required by
# rpi_ws281x, which drives WS2812 timing via DMA+PWM, not bit-banging.
# GPIO18 and GPIO13 (PWM1) are the only two 40-pin-header pins wired to
# a hardware PWM channel; GPIO18 was picked arbitrarily between the two
# (either would work) since nothing else on this module needs PWM.
#
# Direct 3.3V drive, no level shifter - see this module's BUILD.md for
# the accepted-risk note (WS2812B nominally wants ~4.5V+ for a reliable
# "1" bit; many rings still read 3.3V correctly in practice, especially
# over the short wire runs this module uses, but this is a documented
# risk acceptance, not a guarantee).
RING_DATA_PIN = 18
