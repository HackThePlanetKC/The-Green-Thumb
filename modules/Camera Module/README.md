# Camera Module — Visual Health Monitor

A standalone Pi-based module: periodic photos of the plant for visual
health tracking, with a WS2812B RGBW ring for low-light fill flash.
Not a BLE-paired peripheral of a Green Thumb base station (unlike the
base's own `module/<mod_id>/*` MQTT relay contract - see
[`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md)) - this module
runs its own OS, its own MQTT client, and is expected to speak MQTT
discovery directly, independent of any particular base. See that same
doc's `modules/` layout note for why this and future modules live in
their own directories, separate from the base's own `core/`/`drivers/`/
`web/` code.

## Hardware / BOM

| Part | Notes |
|---|---|
| Raspberry Pi Zero 2 W | Runs this module's software (Pi/Linux, not MicroPython) |
| Raspberry Pi Camera Module v3 | Capture hardware - capture scheduling not yet written, see "Remaining" below |
| WS2812B RGBW ring, 7 pixels | Low-light fill flash - **dedicated white channel used for the flash**, not an RGB-mixed approximation (see `ring.py`) |

## Pin assignment

See [`pins.py`](pins.py) — `RING_DATA_PIN = 18` (Pi hardware PWM0), direct 3.3V drive, no level shifter. Full wiring detail: [`BUILD.md`](BUILD.md).

## Driver choice

[`rpi_ws281x`](https://github.com/rpi-ws281x/rpi-ws281x-python) - the reference WS2812 C library's official Python binding - not the CircuitPython/Blinka wrapper (`adafruit-circuitpython-neopixel`). This module runs on plain Raspbian/Linux, not CircuitPython, so the direct binding is the right fit for the platform rather than pulling in Blinka's hardware-abstraction layer for no benefit here. See [`ring.py`](ring.py).

## Configuration

[`config.py`](config.py) — same `DEFAULT_CONFIG` + deep-merge-on-load idiom as the base station's `core/config.py`, kept entirely separate (own `config.json`, own file, no shared state with the base). Currently:

| Key | Default | Notes |
|---|---|---|
| `flash.low_light_threshold` | `50` | Below this reading, a flash fires before capture. Sane starting default, not sourced from a datasheet - tune once mounted and pointed at a real plant/enclosure, same "adjustable, not fixed" spirit as the base station's own thresholds. Units depend on whichever light sensor driver this module ends up using (not written yet). |
| `flash.brightness` | `0.5` | W-channel level (0.0-1.0) while flashing. |

## Flash trigger logic

[`flash_controller.py`](flash_controller.py)'s `maybe_flash(light_level, threshold, exposure_s, ring, brightness, sleep)`: reads the current light level, and if it's below `threshold`, drives the ring's white channel on for `exposure_s` - timed to the camera's exposure for this specific capture, not a fixed constant - then off again. Above threshold, no flash fires. Uses `try`/`finally` around the exposure wait so an exception mid-capture still turns the ring off rather than leaving it lit indefinitely ("do not leave it held on" - see the combined task this shipped under).

## Status

**Early scaffolding - only the flash subsystem exists.** This is scoped narrowly: the ring driver, the flash trigger decision logic, and config. Not yet built:

- Capture scheduling (when a photo actually gets taken)
- The light sensor driver `flash_controller.maybe_flash()` reads from (its reading is passed in, not sourced here yet)
- MQTT client / discovery (so this module can announce itself the way the base's `friendly_name` addition to `device_info` anticipated - see the combined task this shipped under)
- Ring mechanical mounting (see [`BUILD.md`](BUILD.md) for the glare/reflection constraint that governs where it can go)

## Tests

[`tests/test_ring.py`](tests/test_ring.py) and [`tests/test_flash_controller.py`](tests/test_flash_controller.py) — stub/mock tests, no real Pi hardware or `rpi_ws281x` install required. Run directly with `python3 tests/test_ring.py` / `python3 tests/test_flash_controller.py` from this directory.
