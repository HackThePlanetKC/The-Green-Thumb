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

[`flash_controller.py`](flash_controller.py)'s `maybe_flash(light_level, threshold, exposure_s, ring, brightness, sleep)`: reads the current light level, and if it's below `threshold`, drives the ring's white channel on for `exposure_s` - timed to the camera's exposure for this specific capture, not a fixed constant - then off again. Above threshold, no flash fires. Uses `try`/`finally` around the exposure wait so an exception mid-capture still turns the ring off rather than leaving it lit indefinitely ("do not leave it held on" - see the task this shipped under).

## WiFi setup

[`wifi_manager.py`](wifi_manager.py) — API shape mirrors the base station's own `core/wifi.py` `WifiManager` (`has_credentials()`, `set_credentials()`, `connect_sta()`, `is_connected()`, `ip_address()`, `next_backoff_s()`, `start_ap()`/`stop_ap()`), for cross-project consistency - but the implementation is entirely different: this shells out to `nmcli` (NetworkManager, Raspberry Pi OS's default network stack) rather than driving a MicroPython `network.WLAN()` object, and is fully **synchronous**, not `async`, since nothing else in this module runs an asyncio event loop (deliberate - see that file's module docstring for why forcing `async` here for API-shape parity alone wasn't worth it).

At first boot (or whenever WiFi/MQTT-broker credentials aren't both saved yet), this module starts an open setup hotspot (`GreenThumb-Camera-Setup-<camera_id>`, `camera_id` from [`identity.py`](identity.py) - same 6-hex-char derivation as the base station's `base_id`, but its own separate identifier/namespace) and serves [`static/setup.html`](static/setup.html): scan/select/enter a WiFi network, plus the MQTT broker address/port (bundled into the same step - see [Configuration](#configuration) below for why). Credentials are stored in plain JSON, same as the base station's own approach (see `config.py`) - not a new gap introduced here.

**Different AP-mode gateway IP than the base station**, worth knowing if you're used to the base's `192.168.4.1`: this module's hotspot uses NetworkManager's own default, `10.42.0.1` (`wifi_manager.AP_IP`) - a fact about how `nmcli`'s hotspot mode behaves, not a project-wide convention.

## MQTT discovery and base association

[`mqtt_discovery.py`](mqtt_discovery.py) — once online, subscribes to `greenthumb/+/device_info` (the base station's retained "who am I" topic - see `core/mqtt_client.py`'s `publish_device_info()` in the base firmware repo) and builds a live list of known bases as they're seen, keyed by `base_id`. Uses [`paho-mqtt`](https://pypi.org/project/paho-mqtt/) (the standard Pi/Linux MQTT client) with `CallbackAPIVersion.VERSION2` pinned explicitly - not a hand-rolled client like the base's `umqtt.simple` usage, which exists there specifically because MicroPython has no equivalent built in; no such constraint applies here.

[`base_association.py`](base_association.py) — stores which base(s) this module is associated with, as a list of `base_id` strings (**never `friendly_name`**, which is a display-only, user-editable label that can change at any time - see `core/mqtt_client.py`'s `friendly_name` field, which exists specifically so this module has something human-readable to show in the picker instead of a raw `base_id`).

Once WiFi + broker are both configured, [`static/associate.html`](static/associate.html) lists every discovered base by `friendly_name` with a checkbox per base - a base only appears once it's been seen online at least once (its retained `device_info` is what this list is built from). A base may be associated with multiple contiguous grid cells later (grid/settings UI is a separate follow-up task, not built yet - see "Remaining" below).

## Web portal

[`web_portal.py`](web_portal.py) — a minimal HTTP server for the two pages above, deliberately built on Python's standard-library `http.server` rather than a hand-rolled async server like the base station's own `web/server.py`. That approach exists there because MicroPython's whole `main.py` runs one asyncio event loop and a vendored framework's overhead isn't worth it on an ESP32-C3's limited RAM - neither constraint applies on a Pi, and the standard library already ships a working HTTP server, so reimplementing HTTP parsing a second time here would be duplicated effort with no benefit. See that file's module docstring.

`GET /` is context-sensitive, mirroring the base station's own `/` routing: shows the setup page until WiFi + broker are both configured, then shows the association page instead.

## Configuration

[`config.py`](config.py) — same `DEFAULT_CONFIG` + deep-merge-on-load idiom as the base station's `core/config.py`, kept entirely separate (own `config.json`, own file, no shared state with the base). Currently:

| Key | Default | Notes |
|---|---|---|
| `flash.low_light_threshold` | `50` | Below this reading, a flash fires before capture. Sane starting default, not sourced from a datasheet - tune once mounted and pointed at a real plant/enclosure, same "adjustable, not fixed" spirit as the base station's own thresholds. Units depend on whichever light sensor driver this module ends up using (not written yet). |
| `flash.brightness` | `0.5` | W-channel level (0.0-1.0) while flashing. |
| `wifi.ssid` / `wifi.password` | `""` / `""` | Set via the setup portal. Plain JSON, matching the base station's own (non-encrypted) approach. |
| `mqtt.broker` / `mqtt.port` | `""` / `1883` | User-entered, no auto-discovery mechanism (none exists anywhere in this project - the base station's own settings page requires manual broker entry too). Bundled into the WiFi setup step specifically because base discovery - this module's very next setup step - needs a broker connection to work at all. |
| `associated_base_ids` | `[]` | List of `base_id` strings this module is associated with - see [MQTT discovery and base association](#mqtt-discovery-and-base-association) above. |

## Status

**WiFi setup + MQTT discovery/base association are built.** The flash subsystem (ring driver, trigger logic) was built first; this module now also has its own identity, WiFi manager, MQTT discovery, base association, and a minimal setup portal. Not yet built:

- Capture scheduling (when a photo actually gets taken)
- The light sensor driver `flash_controller.maybe_flash()` reads from (its reading is passed in, not sourced here yet)
- Grid/region layout and per-base settings UI (module-global vs. per-base scoping, wilt-watch/drama-level comparison metrics) - explicitly deferred to a separate follow-up task
- This module's own top-level MQTT/HA presence for global settings (`greenthumb/camera/<camera_id>/global/...`) - also part of that follow-up task
- Ring mechanical mounting (see [`BUILD.md`](BUILD.md) for the glare/reflection constraint that governs where it can go)

## Tests

Stub/mock tests, no real Pi hardware, `rpi_ws281x`, `nmcli`/NetworkManager, or MQTT broker required - run any of them directly with `python3 tests/<name>.py` from this directory:

| File | Covers |
|---|---|
| [`tests/test_ring.py`](tests/test_ring.py) | Ring driver - every `set_white()` call is W-channel-only, R/G/B always 0 |
| [`tests/test_flash_controller.py`](tests/test_flash_controller.py) | Flash trigger decision logic, exception-safety |
| [`tests/test_identity.py`](tests/test_identity.py) | `camera_id` derivation from a fake sysfs MAC address |
| [`tests/test_wifi_manager.py`](tests/test_wifi_manager.py) | WiFi scan/connect/status parsing against a fake `nmcli` |
| [`tests/test_mqtt_discovery.py`](tests/test_mqtt_discovery.py) | Retained-message parsing, malformed input handling, connect/disconnect against a fake MQTT client |
| [`tests/test_base_association.py`](tests/test_base_association.py) | Association persistence round trip |
| [`tests/test_web_portal.py`](tests/test_web_portal.py) | HTTP routes end-to-end against a real loopback server (fake collaborators) - status codes, routing, HTML-escaping of user-controlled data |
