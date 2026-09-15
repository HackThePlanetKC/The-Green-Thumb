"""
pins.py - GPIO pin assignments for the Green Thumb base board
(ESP32-C3 Super Mini).

CONFIRMED against a physical board photo (ACEIRMC ESP32-C3 Super Mini,
back-of-board silkscreen: left column 5V/G/3.3/4/3/2/1/0, right column
5/6/7/8/9/10/20/21 - the standard 13-GPIO Super Mini breakout). Every
pin assigned below is physically present on that board. Still worth a
silkscreen check if you're using a different "Super Mini" clone vendor,
since labeling has been reported to vary slightly across them - but this
is no longer a documentation-only proposal for the ACEIRMC board
specifically.

One remaining note, not a physical-availability concern: GPIO4-7 are
described as JTAG-capable by some sources and freely available by
others - fine for this project either way (no hardware JTAG debugging
planned, development uses the native USB REPL).

Deliberately avoided entirely: GPIO2/8/9 (strapping pins - GPIO8 also
drives the onboard LED, GPIO9 is wired to the BOOT button), GPIO18/19
(native USB D-/D+), GPIO20/21 (default UART - repurposing breaks REPL/
serial console access during development), GPIO12-17 (not broken out on
Super Mini boards - internal flash use).

This is a plain constants file, not part of config.py - these are
hardware wiring facts fixed at deployment, not user-editable runtime
settings.
"""

# ADC1-capable (see core/wifi.py notes on ESP32-C3 having no ADC2, so no
# WiFi-vs-ADC conflict regardless of which ADC1 pin is used).
SOIL_MOISTURE_ADC_PIN = 0
LIGHT_SENSOR_ADC_PIN = 1

DHT11_PIN = 3
BUTTON_PIN = 10

# Shared I2C bus for the SSD1306 display (and, later, a BH1750 upgrade
# for the light sensor - see drivers/light_sensor.py's module docstring).
I2C_SDA_PIN = 4
I2C_SCL_PIN = 5

WS2812_PIN = 6
