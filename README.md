# 🌱 Green Thumb

A self-contained, expandable indoor plant growth helper built around an ESP32-C3, with full Home Assistant integration and a standalone web portal for everyone else.

Green Thumb monitors temperature, humidity, soil moisture, and light — cycles the data on a small OLED display, reflects overall plant health through an RGB status LED, and reports everything to Home Assistant over MQTT via a custom HACS integration. No HA? A lightweight web portal served directly from the device covers the same ground.

The base station is intentionally minimal — sensors and reporting only. Actuation (watering, grow lighting, and more) is handled by separate, purpose-built modules that pair to the base wirelessly over Bluetooth Low Energy, so the system grows without the base board ever needing a redesign.

## Open source & community

Green Thumb is fully open source and built for the hacker/maker community. The full base station firmware, MQTT/BLE architecture, and (eventually) the HACS integration and module firmware are all published here — clone it, modify it, build your own modules, adapt it to hardware you already have on hand.

**Licensing note:** this project is released under CC BY-NC-SA 4.0 — free to use, modify, and share for noncommercial purposes with attribution. Commercial use (including selling assembled boards, kits, or modules built from this design) is reserved to the project maintainer. See [`LICENSE.md`](LICENSE.md).

**Fully assembled modules are planned for sale** directly from the maintainer once the base station and first BLE modules (starting with watering) are stable — for people who want the hardware without soldering it themselves. The design stays open regardless; buying an assembled unit is a convenience, not a requirement.

Contributions, issues, and forks are welcome. If you build a module, open a PR — the BLE GATT schema is designed specifically so third-party modules can be added without changing base firmware.

## Why

Most plant monitors either dump raw numbers with no interpretation, or lock you into a single cloud app. Green Thumb is local-first: your data lives in your Home Assistant instance (or on the device itself), thresholds are yours to configure per plant, and the hardware is designed to expand — a pump module, a grow light module, an NPK sensor — without replacing what you already built.

## Hardware (base station)

| Component | Purpose |
|---|---|
| ESP32-C3 Super Mini | Main controller — WiFi/MQTT gateway, BLE central |
| DHT11 | Temperature & humidity |
| Capacitive soil moisture sensor v1.2 | Soil moisture (calibrated per-sensor) |
| Photoresistor (LDR) | Light level — planned upgrade path to BH1750 digital lux sensor |
| SSD1306 128×64 OLED | Cycles through live sensor readings |
| WS2812 addressable RGB LED | Plant health at a glance (green / yellow / red), BLE pairing indicator |
| Pushbutton | Short press: cycle display · Long press: enter BLE pairing mode |

Units throughout: °F and foot-candles.

## How it fits together

```
┌─────────────────────┐         MQTT / WiFi         ┌──────────────────┐
│   Green Thumb Base   │ ───────────────────────────▶│  Home Assistant   │
│  (sensors, display,  │                              │  (Mosquitto +     │
│   status LED, gateway)│                              │   custom HACS     │
│                      │◀──── BLE (central) ─────┐    │   integration)    │
└─────────────────────┘                          │    └──────────────────┘
                                                   │
                              ┌────────────────────┴───────────────────┐
                              │        Future BLE peripheral modules     │
                              │   watering pump · grow light · NPK sensor │
                              └───────────────────────────────────────────┘
```

Modules never touch WiFi or Home Assistant directly — the base is the only gateway. This keeps the network surface small and means a module can be designed, built, and paired without ever modifying base firmware.

## Status: early build, architecture complete

All core design decisions — MQTT topic structure, health-threshold logic, calibration flows, BLE pairing and GATT schema — are finalized. Firmware implementation is in progress, driver-by-driver. Full technical detail: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

### Done
- [x] Full architecture spec (MQTT topics, timing, thresholds, calibration & pairing state machines, BLE GATT schema)
- [x] `core/storage.py` — atomic flash read/write
- [x] `core/config.py` — config schema, defaults, load/save with forward-compatible merging
- [x] `drivers/dht11.py` — temperature/humidity
- [x] `drivers/soil_moisture.py` — soil moisture with two-point calibration
- [x] `drivers/light_sensor.py` — LDR light level (placeholder calibration pending a lux reference)

### In progress / up next
- [x] `drivers/display.py` — OLED screen cycling + night mode
- [x] `drivers/status_led.py` — WS2812 health colors + pairing indicator
- [x] `drivers/button.py` — short/long press handling
- [x] `core/wifi.py`, `core/identity.py` — connectivity & base_id derivation
- [ ] `core/ntp.py` — time sync
- [ ] `core/mqtt_client.py` — topic builder, pub/sub wrapper, LWT
- [ ] `core/health.py` — green/yellow/red calculation
- [ ] `core/light_tracker.py` — daily light-hours accumulator
- [ ] `core/calibration.py` — soil/light calibration state machine
- [ ] `core/pairing.py`, `core/ble_central.py`, `core/module_manager.py` — BLE module support
- [ ] `main.py` / `boot.py` — asyncio task orchestration
- [ ] `web/` — standalone portal for non-HA users
- [ ] Custom HACS integration (separate repo/component)
- [ ] First BLE peripheral module: watering pump
- [ ] GPIO pin map finalization
- [ ] Light sensor real-world calibration (needs a lux reference)
- [ ] High-intensity/sunburn light thresholds (`light_fc.red_min`/`red_max`) — no sourced data yet

## Design principles

- **Local-first.** Home Assistant + Mosquitto is the primary integration path; the device works without any cloud dependency.
- **Spec before code.** Every subsystem — MQTT schema, calibration, pairing — was fully designed before implementation started.
- **No guessed thresholds.** Health ranges are either sourced from horticultural references or explicitly left unset rather than filled with a plausible-looking placeholder.
- **The base never redesigns.** New capabilities arrive as BLE modules, not base firmware rewrites.

## License

CC BY-NC-SA 4.0 — see [`LICENSE.md`](LICENSE.md). Commercial use reserved to the maintainer.
