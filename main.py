"""
main.py - entry point. Wires every driver and core module built so far
into a running set of asyncio tasks.

Startup order matters here in one specific way: the boot-hold check
(wifi.check_setup_hold_at_boot) MUST run before the asyncio event loop
starts - see that function's own docstring for why (a runtime button
hold could fire by accident; a hold-during-power-on cannot). Everything
else is instantiated inside main(), in dependency order, using Python's
normal closure semantics - a callback defined early that references a
name assigned later in the same function scope works fine as long as
the callback isn't actually CALLED until after that assignment
completes, which is true for every callback registered below (they're
all just handed to constructors or scheduled as tasks, not invoked
immediately).

Two things resolved here that were flagged as open items elsewhere,
not newly discovered:

1. Night mode / unsynced RTC (see docs/ARCHITECTURE.md): display.py's
   now_provider is wrapped (_display_now_provider below) rather than
   passed ntp_sync.local_time directly - while unsynced, it returns a
   fixed midday time (12, 0), which for any reasonable night-mode
   window (e.g. 22:00-07:00) evaluates as NOT night, so the display
   fails safe to "stay on" rather than risk going dark based on a
   meaningless (0,0) unsynced-clock reading.

2. A sync/async mismatch, same class of bug as the one caught earlier
   between pairing.py and module_manager.py: mqtt_client's
   on_module_command callback is invoked SYNCHRONOUSLY (from
   check_msg()'s dispatch chain), but module_manager.send_command() is
   async. Passing it directly would silently create a coroutine object
   that never runs. _handle_module_command below wraps it in
   asyncio.create_task() instead - fire-and-forget, matching how
   pairing.py's own start() already launches its scan as a background
   task rather than being awaited inline.
"""

import asyncio
import time

from machine import I2C, Pin

import pins
import config as config_module
import identity
import wifi
import ntp
import mqtt_client
import health
import light_tracker
import calibration
import pairing
import ble_central
import module_manager

import dht11
import soil_moisture
import light_sensor
import display as display_driver
import status_led as status_led_driver
import button as button_driver
import icons

import server as web_server_module


# --- Boot-hold check: BEFORE asyncio starts, see module docstring ---
_setup_mode_requested = wifi.check_setup_hold_at_boot(pins.BUTTON_PIN, hold_s=3)


def _is_night_now(night_cfg, hour, minute):
    """
    Same wraparound-aware logic as drivers/display.py's private
    _is_night_mode, reimplemented here (not imported - that method is
    intentionally private to Display) since status_led.py's tick() needs
    the same "is it night right now" answer independently, on its own
    tick cadence, and takes it as a plain bool rather than owning its
    own clock/config lookup.
    """
    if not night_cfg.get("enabled"):
        return False
    now_min = hour * 60 + minute
    start_min = night_cfg["start_hour"] * 60 + night_cfg["start_minute"]
    end_min = night_cfg["end_hour"] * 60 + night_cfg["end_minute"]
    if start_min == end_min:
        return False
    if start_min < end_min:
        return start_min <= now_min < end_min
    return now_min >= start_min or now_min < end_min


async def main():
    cfg = config_module.load()
    base_id = identity.get_base_id()

    # --- Drivers ---
    dht = dht11.DHT11Sensor(pins.DHT11_PIN)
    soil = soil_moisture.SoilMoistureSensor(pins.SOIL_MOISTURE_ADC_PIN)
    light = light_sensor.LDRLightSensor(pins.LIGHT_SENSOR_ADC_PIN)

    i2c = I2C(0, scl=Pin(pins.I2C_SCL_PIN), sda=Pin(pins.I2C_SDA_PIN))

    def _display_now_provider():
        # See module docstring, item 1.
        if not ntp_sync.is_synced():
            return (12, 0)
        return ntp_sync.local_time()

    disp = display_driver.Display(
        i2c,
        now_provider=_display_now_provider,
        auto_cycle_interval_s=cfg["display"]["auto_cycle_interval_s"],
        resume_idle_s=cfg["display"]["resume_idle_s"],
        night_mode_cfg=cfg["display"]["night_mode"],  # live reference - set_config mutations apply automatically
    )

    def _handle_escalation_change(is_escalated):
        latest["requires_immediate_attention"] = is_escalated
        mqtt.publish_health(_build_health_payload())

    led = status_led_driver.StatusLed(
        pins.WS2812_PIN,
        brightness=cfg["status_led"]["brightness"],
        blink_interval_ms=cfg["status_led"]["blink_interval_ms"],
        red_escalation_delay_s=cfg["status_led"]["red_escalation_delay_s"],
        on_escalation_change=_handle_escalation_change,
    )

    def _start_pairing_from_button():
        pairing_mgr.start()
        mqtt.publish_pairing_status(pairing_mgr.status())
        led.set_pairing_mode(pairing_mgr.status()["state"] in ("scanning", "awaiting_select"))

    btn = button_driver.Button(
        pins.BUTTON_PIN,
        on_short_press=disp.manual_next,
        on_long_press=_start_pairing_from_button,
    )

    # --- Connectivity ---
    wifi_mgr = wifi.WifiManager(cfg["wifi"]["ssid"], cfg["wifi"]["password"])
    ntp_sync = ntp.NtpSync(utc_offset_hours=cfg["timezone"]["utc_offset_hours"])

    # --- Tracking / calibration / BLE ---
    tracker = light_tracker.LightTracker(sample_interval_s=cfg["timing"]["sample_interval_s"])
    cal_mgr = calibration.CalibrationManager(cfg, soil, light)

    # --- Shared mutable state, updated by sensor_loop, read by
    # display screens / web dashboard / publish_loop ---
    latest = {
        "temp_f": None, "humidity_pct": None, "soil_moisture_pct": None,
        "light_fc": None, "light_level_label": None,
        "health": {"status": None, "reasons": []},
        "requires_immediate_attention": False,
    }

    def _build_health_payload():
        """
        Single source of truth for what gets published to the health
        MQTT topic - used by every publish call site (escalation
        callback, sensor_loop's red-transition interrupt publish,
        publish_loop's periodic publish) specifically so they can't
        silently disagree on which fields are included. An earlier
        version had three separate call sites building this payload
        independently; two of them omitted requires_immediate_attention
        entirely, meaning the next periodic publish after an escalation
        (up to 30 min later, since health is a retained topic) silently
        overwrote the retained flag and lost it, even though the LED
        was still physically blinking red.
        """
        payload = dict(latest["health"] or {"status": None, "reasons": []})
        payload["requires_immediate_attention"] = latest["requires_immediate_attention"]
        return payload

    # mqtt must be constructed BEFORE module_mgr/pairing_mgr below, since
    # module_mgr's constructor takes it as a direct argument value
    # (evaluated immediately), unlike the callback closures here, which
    # forward-reference module_mgr/pairing_mgr safely - those aren't
    # evaluated until actually called, long after this whole function
    # finishes setting up. Direct constructor arguments don't get that
    # same forgiveness - an earlier version of this file got this wrong
    # and threw UnboundLocalError, caught during smoke testing.

    def _handle_calibration_command(payload):
        cal_mgr.handle_command(payload)
        mqtt.publish_calibration_status(cal_mgr.status())

    def _handle_pairing_command(payload):
        pairing_mgr.handle_command(payload)
        mqtt.publish_pairing_status(pairing_mgr.status())
        led.set_pairing_mode(pairing_mgr.status()["state"] in ("scanning", "awaiting_select"))

    def _handle_module_command(mod_id, payload):
        # See module docstring, item 2 - must not call the async
        # send_command directly from this synchronous callback.
        asyncio.create_task(module_mgr.send_command(mod_id, payload))

    def _get_light_hours_for_mqtt():
        return {"light_hours_today": tracker.get_light_hours_today()}

    mqtt = mqtt_client.GreenThumbMqtt(
        cfg["mqtt"]["broker"], cfg["mqtt"]["port"], cfg,
        username=cfg["mqtt"]["username"], password=cfg["mqtt"]["password"],
        on_command=None,  # no other base-level command actions implemented yet
        on_calibration_command=_handle_calibration_command,
        on_pairing_command=_handle_pairing_command,
        on_module_command=_handle_module_command,
        on_get_light_hours_today=_get_light_hours_for_mqtt,
    )

    module_mgr = module_manager.ModuleManager(ble_central, mqtt, on_module_health=None)
    pairing_mgr = pairing.PairingManager(ble_central, module_mgr, timeout_s=cfg["timing"]["pairing_timeout_s"])

    # --- Web portal providers (see web/server.py's documented contracts) ---
    def _get_dashboard_state():
        return {
            "temp_f": latest["temp_f"],
            "humidity_pct": latest["humidity_pct"],
            "soil_moisture_pct": latest["soil_moisture_pct"],
            "light_fc": latest["light_fc"],
            "light_level_label": latest["light_level_label"],
            "health": latest["health"],
            "wifi_ip": wifi_mgr.ip_address(),
            "mqtt_connected": mqtt.is_connected(),
            "modules": [_build_module_summary(mod_id) for mod_id in module_mgr.known_module_ids()],
        }

    def _build_module_summary(mod_id):
        """
        needs_calibration/calibration_fields are provisional, module-
        defined (see docs/ARCHITECTURE.md) - main.py doesn't interpret
        them, just passes through whatever the module's own State JSON
        reports, defaulting gracefully if the module hasn't sent any
        state yet (get_last_state() returns None right after pairing,
        before relay_forever() has connected - see that method's docstring).
        """
        last_state = module_mgr.get_last_state(mod_id) or {}
        return {
            "mod_id": mod_id,
            "type": module_mgr.get_by_mod_id(mod_id)["type"],
            "online": module_mgr.is_module_online(mod_id),
            "needs_calibration": bool(last_state.get("needs_calibration")),
            "calibration_fields": last_state.get("calibration_fields", []),
        }

    def _get_light_hours_today_value():
        return tracker.get_light_hours_today()

    def _sample_light_now():
        # Blocking ~2s (10 samples averaged) - accepted tradeoff for an
        # infrequent manual "Sample Now" button click, same class of
        # tradeoff already made for wifi.connect_sta()/ntp.sync_once().
        return light.read_raw_averaged()

    web = web_server_module.WebServer(
        cfg, wifi_mgr,
        state_provider=_get_dashboard_state,
        light_hours_provider=_get_light_hours_today_value,
        mqtt_manager=mqtt,
        light_raw_sample_provider=_sample_light_now,
        calibration_manager=cal_mgr,
        calibration_command_handler=_handle_calibration_command,
        pairing_manager=pairing_mgr,
        pairing_command_handler=_handle_pairing_command,
        module_manager=module_mgr,
    )

    # --- Display screens ---
    def _screen_temp_humidity(d):
        t, h = latest["temp_f"], latest["humidity_pct"]
        line = "{}F  {}%".format(
            round(t) if t is not None else "--",
            round(h) if h is not None else "--",
        )
        d.draw_screen(icons.ICON_TEMP, "Temp/Hum", [line])

    def _screen_soil(d):
        s = latest["soil_moisture_pct"]
        line = "{}%".format(round(s) if s is not None else "--")
        d.draw_screen(icons.ICON_DROPLET, "Soil", [line])

    def _screen_light(d):
        label = latest["light_level_label"]
        if label:
            line = label
        else:
            l = latest["light_fc"]
            line = "{} fc".format(round(l) if l is not None else "--")
        d.draw_screen(icons.ICON_SUN, "Light", [line])

    def _screen_health(d):
        status = (latest["health"] or {}).get("status")
        line = status.upper() if status else "..."
        d.draw_screen(icons.ICON_HEALTH, "Health", [line])

    def _screen_wifi(d):
        ip = wifi_mgr.ip_address()
        d.draw_screen(icons.ICON_WIFI, "WiFi", [ip if ip else "not connected"])

    disp.set_screens([
        ("temp_humidity", _screen_temp_humidity),
        ("soil", _screen_soil),
        ("light", _screen_light),
        ("health", _screen_health),
        ("wifi", _screen_wifi),
    ])

    # --- Background tasks ---

    async def sensor_loop():
        """Local sampling cadence (config.timing.sample_interval_s, 2min default)."""
        while True:
            dht_result = dht.read()
            temp_f, humidity_pct = dht_result if dht_result is not None else (None, None)

            soil_raw = soil.read_raw_averaged()
            soil_pct = soil.read_percent(
                soil_raw, cfg["soil_calibration"]["dry_raw"], cfg["soil_calibration"]["wet_raw"]
            )

            light_raw = light.read_raw_averaged()
            light_fc = light.read_fc(
                light_raw,
                cfg["light_calibration"]["dark_raw"], cfg["light_calibration"]["bright_raw"],
                cfg["light_calibration"]["bright_fc"], cfg["light_calibration"]["calibrated"],
            )

            light_level_label = None
            if cfg["light_mode"]["mode"] == "multi_point":
                light_level_label = light_sensor.classify_light_level(light_raw, cfg["light_mode"]["points"])

            readings = {
                "temp_f": temp_f, "humidity_pct": humidity_pct,
                "soil_moisture_pct": soil_pct, "light_fc": light_fc,
            }
            health_result = health.evaluate_health(readings, cfg["thresholds"])

            previous_status = latest["health"].get("status") if latest["health"] else None

            latest["temp_f"] = temp_f
            latest["humidity_pct"] = humidity_pct
            latest["soil_moisture_pct"] = soil_pct
            latest["light_fc"] = light_fc
            latest["light_level_label"] = light_level_label
            latest["health"] = health_result

            if health_result["status"] is not None:
                led.set_health(health_result["status"])
                if health_result["status"] == "red" and previous_status != "red":
                    # Interrupt publish - immediate, only on transition to red (see docs/ARCHITECTURE.md)
                    mqtt.publish_health(_build_health_payload())

            if light_fc is not None:
                tracker.record_sample(light_fc, cfg["thresholds"], cfg["light_tracking"])

            if ntp_sync.is_synced():
                rollover_summary = tracker.check_rollover(
                    ntp_sync.local_date(), cfg["thresholds"], cfg["light_tracking"]
                )
                if rollover_summary is not None:
                    mqtt.publish_light_summary(rollover_summary)

            await asyncio.sleep(cfg["timing"]["sample_interval_s"])

    async def publish_loop():
        """MQTT publish cadence (config.timing.publish_interval_s, 30min default)."""
        while True:
            if mqtt.is_connected():
                mqtt.publish_state({
                    "temp_f": latest["temp_f"], "humidity_pct": latest["humidity_pct"],
                    "soil_moisture_pct": latest["soil_moisture_pct"], "light_fc": latest["light_fc"],
                })
                mqtt.publish_health(_build_health_payload())
            await asyncio.sleep(cfg["timing"]["publish_interval_s"])

    async def display_tick_loop():
        while True:
            disp.tick()
            await asyncio.sleep(1)

    async def led_tick_loop():
        while True:
            night_cfg = cfg["display"]["night_mode"]
            if ntp_sync.is_synced():
                hour, minute = ntp_sync.local_time()
                night_active = _is_night_now(night_cfg, hour, minute)
            else:
                night_active = False  # fail safe: unsynced clock never suppresses the LED
            led.tick(
                night_mode_active=night_active,
                led_off_in_night_mode=night_cfg["led_off"],
                red_overrides_led_off=night_cfg["red_overrides_led_off"],
            )
            await asyncio.sleep(0.2)

    async def button_poll_loop():
        while True:
            btn.poll()
            await asyncio.sleep(0.02)

    async def wifi_connect_loop():
        while True:
            if not wifi_mgr.is_connected():
                if wifi_mgr.has_credentials():
                    led.set_wifi_connecting(True)
                    success = await wifi_mgr.connect_sta()
                    led.set_wifi_connecting(False)
                    if not success:
                        await asyncio.sleep(wifi_mgr.next_backoff_s())
                        continue
            await asyncio.sleep(5)

    async def pairing_status_loop():
        """
        Polls pairing_mgr.status() to catch the async scan's eventual
        completion (start() launches a background task - see
        pairing.py - so the transition to "awaiting_select" doesn't
        happen synchronously within any callback here). Synchronous
        transitions (select/cancel/acknowledge) already publish
        immediately via _handle_pairing_command above; this loop is
        specifically for the one transition that can't be caught that way.
        """
        last_status = None
        while True:
            status = pairing_mgr.status()
            led.set_pairing_mode(status["state"] in ("scanning", "awaiting_select"))
            if status != last_status:
                mqtt.publish_pairing_status(status)
                last_status = status
            await asyncio.sleep(1)

    # --- Schedule everything ---
    asyncio.create_task(ntp_sync.sync_forever())
    asyncio.create_task(mqtt.reconnect_forever())
    asyncio.create_task(mqtt.listen_forever())
    asyncio.create_task(module_mgr.relay_forever())
    asyncio.create_task(sensor_loop())
    asyncio.create_task(publish_loop())
    asyncio.create_task(display_tick_loop())
    asyncio.create_task(led_tick_loop())
    asyncio.create_task(button_poll_loop())
    asyncio.create_task(wifi_connect_loop())
    asyncio.create_task(pairing_status_loop())

    if _setup_mode_requested or not wifi_mgr.has_credentials():
        wifi_mgr.start_ap()

    await web.start()

    # All real work happens in the background tasks above - this just
    # keeps the coroutine (and therefore the event loop) alive.
    while True:
        await asyncio.sleep(3600)


asyncio.run(main())
