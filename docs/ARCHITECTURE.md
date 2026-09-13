# Green Thumb — Architecture & Design Decisions

Full technical spec: MQTT topics, thresholds, calibration/pairing state machines, BLE GATT schema, and every design decision made along the way. See [`README.md`](../README.md) for the project overview.

Self-contained indoor plant growth helper. ESP32-C3 Super Mini base board, MicroPython firmware, MQTT to a Home Assistant Mosquitto broker via custom HACS integration. Expandable via BLE-connected peripheral modules.

## Hardware (base board)

- ESP32-C3 Super Mini
- DHT11 (temp/humidity)
- Analog capacitive soil moisture sensor v1.2
- Photoresistor (LDR) light sensor - planned swap to BH1750 I2C lux sensor
- SSD1306 128x64 OLED display (I2C)
- WS2812 addressable RGB LED (status/health color, pairing indicator)
- Physical pushbutton (short press: cycle display / long press 3s: BLE pairing mode)

Units: imperial (°F, foot-candles).

## Architecture

- Firmware: MicroPython, asyncio-based task loop
- Base board is the sole WiFi/MQTT/HA gateway - modules never connect to WiFi or HA directly
- Base acts as BLE central; future modules (pump, grow light, NPK sensor) are BLE peripherals
- HA integration: custom HACS component (not ESPHome, not generic MQTT discovery)
- Fallback: lightweight web portal served from the ESP32 for non-HA users, using the same internal command state machines as the HACS integration (no duplicate logic paths)

## Repo layout

```
/boot.py                  # minimal, runs before main (not yet written)
/main.py                  # entry point, starts asyncio tasks (not yet written)
/lib/                      # vendored third-party libraries (not yet added)
  ssd1306.py                # display driver
  umqtt/robust.py           # MQTT client (auto-reconnect)
  aioble/                   # async BLE library
/drivers/
  dht11.py                  # done
  soil_moisture.py          # done
  light_sensor.py           # done (LDR, placeholder calibration - see open items)
  display.py                # done (screen cycling, night mode, dynamic screen list)
  icons.py                  # done (16x16 monochrome bitmap icons: temp, droplet, sun, health, wifi, module)
  status_led.py             # done (health colors, red escalation to blinking, pairing indicator, night mode interaction)
  button.py                 # done (short/long/very-long press, debounce, dead zone)
/core/
  storage.py                # done - atomic flash read/write helpers
  config.py                 # done - config schema, defaults, load/save
  identity.py                # done (base_id derived from WiFi MAC, shared by wifi.py and future mqtt_client.py)
  wifi.py                   # done (async STA connect/retry, open AP fallback for setup)
  ntp.py                    # not yet written
  mqtt_client.py             # not yet written
  health.py                  # not yet written
  light_tracker.py           # not yet written
  calibration.py             # not yet written
  pairing.py                 # not yet written
  ble_central.py              # not yet written
  module_manager.py           # not yet written
/web/
  server.py                   # not yet written
  static/                     # not yet written
```

## Key design decisions

**Timing:**
- Local sensor sampling: every 2 min
- MQTT state publish: every 30 min default, runtime-adjustable via command
- Interrupt publish: immediate, only on transition to red health status
- Display auto-cycle: every 10s, resumes 20s after a manual button press
- Display night mode: configurable time range (default 22:00-07:00), display off outside manual wake; a button press during night mode wakes to that screen for 20s (same idle timer as normal), then re-sleeps

**MQTT topic tree:**
```
greenthumb/<base_id>/status                      (LWT, retained)
greenthumb/<base_id>/state                        (JSON, retained)
greenthumb/<base_id>/health                       (JSON, retained)
greenthumb/<base_id>/config                       (JSON, retained - full current user-configurable settings, republished after every change)
greenthumb/<base_id>/command                      (JSON, not retained)
greenthumb/<base_id>/light_summary                (JSON, retained, published daily at midnight)
greenthumb/<base_id>/calibration/command
greenthumb/<base_id>/calibration/status           (retained)
greenthumb/<base_id>/pairing/command
greenthumb/<base_id>/pairing/status               (retained)
greenthumb/<base_id>/module/<mod_id>/status
greenthumb/<base_id>/module/<mod_id>/state
greenthumb/<base_id>/module/<mod_id>/command
```
QoS 1 everywhere. `<base_id>`/`<mod_id>` derived from MAC address.

**HA-visible/writable configuration:** every user-configurable setting (thresholds, calibration values, timing, display/night-mode) is readable via the retained `config` topic and writable via a single generic command:
```json
{"action": "set_config", "path": "thresholds.temp_f.green_min", "value": 66}
```
`path` uses dot notation into the config schema (see `core/config.py`'s `get_by_path`/`set_by_path`). An invalid or typo'd path is rejected (`KeyError`) rather than silently ignored or creating a new key. This replaces per-setting bespoke commands (earlier drafts had `set_interval`, `set_night_mode` individually) with one mechanism that covers the whole schema.

**Explicitly excluded from HA-visible config:** WiFi credentials and MQTT broker address/credentials. The device can't receive MQTT commands before it already has WiFi and a broker connection, so exposing those over MQTT is circular, and broker credentials shouldn't travel over MQTT regardless. These stay in the initial-setup web portal flow only.

**Open item:** what happens on an invalid `set_config` path or value (reject silently, publish an error somewhere, echo failure in the `config` topic) isn't decided yet - to be settled when `core/mqtt_client.py`'s command handler is built.

**Health thresholds** (user-configurable per plant profile, generic houseplant defaults shown):

| Metric | Green | Red | Source |
|---|---|---|---|
| Temperature | 65-75°F | <50°F or >90°F | Sourced (chilling injury / heat stress references) |
| Humidity | 40-60% | <20% or >80% | Sourced |
| Light | 200-500 fc | **unset - see open items** | Green range sourced; red thresholds not yet sourced |
| Soil moisture | 20-40% | <10% or >50% | User-specified |

**Soil moisture calibration:** MQTT-driven state machine (`start` -> `read_dry` -> `read_wet` -> `save`/`cancel`), 10 samples averaged over ~2s per read. Shared between web portal and HACS integration - one state machine, two entry points. Moisture % computed via linear interpolation between the two calibrated raw readings (dry_raw -> 0%, wet_raw -> 100%) - sensor polarity not assumed, only the two labeled endpoints. Raw values are on the `machine.ADC.read_u16()` scale (0-65535, portable across MicroPython ports), not a raw 12-bit ADC count.

**Light duration tracking:** separate from instantaneous fc reading.
- `light_present_threshold_fc`: 75 (sourced - "just enough daylight to read by")
- `light_hours_target`: 12-16h/day (sourced)
- `high_intensity_hours` (sunburn risk tracking): schema in place but inert - blocked on `light_fc.red_max`
- Evaluated once daily at midnight rollover (NTP), not live - status reflects "yesterday"

**Display screens:** confirmed core order, each with a 16x16 monochrome bitmap icon (`drivers/icons.py`), rendered via `Display.draw_screen(icon_bytes, title, lines)`:

| # | Screen | Icon |
|---|---|---|
| 1 | Temp + Humidity | Thermometer |
| 2 | Soil Moisture % | Droplet |
| 3 | Light (fc) | Sun |
| 4 | Overall health status | Heart |
| 5 | WiFi/MQTT connection + IP | WiFi arcs |

**Extensible module screens:** `Display.add_screen(screen_id, render_fn)` / `remove_screen(screen_id)` let future BLE modules insert their own screen when they pair and remove it when they unpair, without `display.py` or `main.py` knowing about module types in advance. `draw_screen()` is the standard format any module's render_fn should target - an icon (use `icons.ICON_MODULE` as a generic fallback if the module has no custom icon), a title, and up to 5 lines of body text. `display.py` has no built-in knowledge of any module's payload shape; `module_manager.py` (not yet written) supplies the actual per-module-type render function when a module pairs. Whether module screens carry their own custom icon or always use the generic gear fallback is not yet decided - deferred until the first real module (watering pump) is built.

**Display night mode:** optional (`display.night_mode.enabled`, default on but fully user-toggleable), runtime-adjustable from Home Assistant via the generic `set_config` command, e.g.:
```json
{"action": "set_config", "path": "display.night_mode.enabled", "value": false}
```
**Status LED (`status_led.py`):** single WS2812, diffused under a thin printed section of the enclosure (a 3D-printed near-life-size fist/thumbs-up; LED sits under the thumbnail, display sits on the middle finger's face). Solid green/yellow/red for health status. If red persists continuously past a configurable delay (`status_led.red_escalation_delay_s`, default 3600s - a UX choice, not a sourced threshold), the LED switches from solid to **blinking** red and fires a `requires_immediate_attention` flag, added to the `health` MQTT payload:
```json
{
  "status": "red",
  "reasons": ["soil_moisture_low"],
  "requires_immediate_attention": true
}
```
This flag flip (either direction) also triggers an immediate out-of-cycle publish, same as the original red-transition rule. Pairing mode (blinking blue) overrides the health display entirely while active, and always bypasses night-mode suppression - it's a deliberate action the user just triggered. WiFi connecting/reconnecting shows as **breathing purple** (smooth sine-based brightness ramp, not a hard blink) - unlike pairing, this respects night-mode suppression, since a reconnect can happen unattended at any hour and there's no reason to light up a dark room over something the user didn't initiate. Night mode's `led_off` suppresses the LED, but **only the escalated/blinking red tier** can override that suppression, and only if `display.night_mode.red_overrides_led_off` is enabled (default off) - plain solid red never overrides night mode, and neither does the breathing-purple WiFi state. `status_led.brightness` (default 0.15, a 0.0-1.0 scalar) is a placeholder pending real tuning once the physical diffused enclosure exists.

Priority order in `tick()`: pairing > WiFi connecting > health display.

`display.night_mode.led_off` additionally suppresses the WS2812 status LED during the night-mode window - see above for the full escalation/override interaction.

**Button (`button.py`):** polled (not IRQ-based) from an asyncio task, ~20ms recommended interval. Press bands: <50ms ignored (bounce), 50-1000ms = short press (fires `display.manual_next` on release), 1000-3000ms = dead zone (ignored entirely, avoids an imprecise release accidentally triggering either action), >=3000ms = long press (fires `pairing.start` once, while still held - not on release). Debounce window: 50ms. Re-entering WiFi setup mode is deliberately **not** a runtime press band - see below.

**WiFi (`wifi.py` + `identity.py`):** async STA connection (never blocks other tasks during connect attempts). On failure, caller applies capped exponential backoff (`next_backoff_s()`: 1s, 2s, 4s... capped at 60s) between retries. A dropped connection after initial success keeps retrying STA in the background rather than falling back to AP - sensors/display/BLE keep working without WiFi; only MQTT/HA connectivity degrades until reconnect. AP (setup) mode is an **open network** (no password, simplest for initial setup), SSID `GreenThumb-Setup-<base_id>`, entered automatically if no credentials are saved at boot. AP+STA run concurrently (ESP32 supports both simultaneously), so entering setup mode doesn't interrupt an existing connection. `base_id` (used here and in the MQTT topic tree) is the last 3 bytes of the WiFi MAC, uppercase hex - `core/identity.py`, shared by both.

**Re-entering setup mode: boot-hold, not a runtime press.** `wifi.check_setup_hold_at_boot(pin_num, hold_s=3)` is a blocking, one-shot check called from `main.py`'s startup sequence before the asyncio event loop starts - if the button is already held down at power-on and stays held for the full duration, the device boots into setup/AP mode instead of normal operation. This is intentionally separate from `button.py`'s runtime press detection: a runtime long-press (however long the threshold) could fire by accident if the device gets pinned against something during normal operation, which would be a serious problem for a device shaped like a fist meant to sit on a shelf. A hold-during-power-on cannot happen by accident.

**BLE pairing flow:** triggered by button long-press (3s) OR remote MQTT command. 60s scan window, blinking blue LED. Explicit user confirmation required even with only one candidate found (avoids accidentally pairing a neighbor's device).

**BLE GATT schema** (module side):
```
Service: a1e50000-b5a3-4393-b673-5d2a1d3d0001
├── Device Type      (...0002) Read
├── Module Serial     (...0003) Read
├── State              (...0004) Read, Notify   - mirrors MQTT module/state JSON shape
├── Command            (...0005) Write           - mirrors MQTT module/command JSON shape
└── Firmware Version   (...0006) Read
```
MTU negotiation attempted at connect; falls back to chunked fragments (header byte = sequence number, 0xFF = final) if negotiation fails.

**Board choice:** ESP32-C3 Super Mini, confirmed after comparing against Pico W/2W (BLE+WiFi share a bus to an external co-processor chip - more contention risk), Pi Zero W/2W (full Linux, would require abandoning the MicroPython architecture), Wemos D1 Mini (ESP8266, no BLE at all), and ESP32-S3/original ESP32 (dual-core alternatives at similar price - would reduce CPU-level WiFi/BLE contention, but user has C3 units on hand already).

## Open items

- `light_fc.red_min` / `light_fc.red_max` (sunburn/high-intensity thresholds) - **unset, no sourced data yet**. Blocks `high_intensity_hours` tracking from having any real effect.
- Pump and grow light removed from base board scope entirely - deferred to a future BLE-connected watering/lighting module.
- Sensor loop failure-escalation policy not yet decided (e.g. what happens after N consecutive DHT11 read failures).
- GPIO pin map not yet finalized across all sensors/peripherals.
- Light sensor (LDR) calibration is unset (`light_calibration.calibrated: False`, placeholder raw/fc values in `config.py`). No ADC-to-foot-candle formula exists for a generic photoresistor - real calibration (dark_raw, bright_raw, bright_fc) must be done via the UI during initial device setup, or when a lux reference becomes available.
