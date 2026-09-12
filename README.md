# Green Thumb

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
  soil_moisture.py          # not yet written
  light_sensor.py           # not yet written
  display.py                # not yet written
  status_led.py             # not yet written
  button.py                 # not yet written
/core/
  storage.py                # done - atomic flash read/write helpers
  config.py                 # done - config schema, defaults, load/save
  wifi.py                   # not yet written
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
- Display auto-cycle resumes 20s after a manual button press

**MQTT topic tree:**
```
greenthumb/<base_id>/status                      (LWT, retained)
greenthumb/<base_id>/state                        (JSON, retained)
greenthumb/<base_id>/health                       (JSON, retained)
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

**Health thresholds** (user-configurable per plant profile, generic houseplant defaults shown):

| Metric | Green | Red | Source |
|---|---|---|---|
| Temperature | 65-75°F | <50°F or >90°F | Sourced (chilling injury / heat stress references) |
| Humidity | 40-60% | <20% or >80% | Sourced |
| Light | 200-500 fc | **unset - see open items** | Green range sourced; red thresholds not yet sourced |
| Soil moisture | 20-40% | <10% or >50% | User-specified |

**Soil moisture calibration:** MQTT-driven state machine (`start` -> `read_dry` -> `read_wet` -> `save`/`cancel`), 10 samples averaged over ~2s per read. Shared between web portal and HACS integration - one state machine, two entry points. Moisture % computed via min/max of the two calibrated raw readings (sensor polarity not assumed).

**Light duration tracking:** separate from instantaneous fc reading.
- `light_present_threshold_fc`: 75 (sourced - "just enough daylight to read by")
- `light_hours_target`: 12-16h/day (sourced)
- `high_intensity_hours` (sunburn risk tracking): schema in place but inert - blocked on `light_fc.red_max`
- Evaluated once daily at midnight rollover (NTP), not live - status reflects "yesterday"

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
