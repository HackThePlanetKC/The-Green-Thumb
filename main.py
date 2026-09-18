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


# Standard deuteranopia/protanopia-safe substitute for the green/red
# health tiers - blue for "good," orange for "critical." Yellow is left
# alone; it's already distinguishable from both without red-green
# confusion. See config.color_scheme in core/config.py.
_COLORBLIND_HEALTH_COLORS = {"green": (0, 120, 255), "yellow": (255, 180, 0), "red": (255, 140, 0)}


def _format_temp(temp_f, unit):
    """
    Display-only conversion for the OLED/dashboard live reading - never
    applied to thresholds.temp_f or any stored/calibration value, which
    stay in °F internally regardless of this preference (see
    config.display.temp_unit and docs/ARCHITECTURE.md). Returns a
    rounded int and its unit letter, or (None, unit) if temp_f itself
    is None (sensor not yet read/failing) - callers format the "--"
    placeholder themselves, consistent with how every other reading
    already handles a None value.
    """
    if temp_f is None:
        return None, unit
    if unit == "C":
        return round((temp_f - 32) * 5 / 9), "C"
    return round(temp_f), "F"


def _resolve_health_colors(scheme_name, custom_colors):
    """
    Translates a config.color_scheme.{web_portal,alert_led} value into
    the {"green","yellow","red"} RGB dict status_led.tick()'s
    health_colors argument expects, or None for "default" (letting
    status_led.py fall back to its own built-in defaults rather than
    duplicating them here).
    """
    if scheme_name == "colorblind":
        return _COLORBLIND_HEALTH_COLORS
    if scheme_name == "custom":
        return {
            "green": tuple(custom_colors["green"]),
            "yellow": tuple(custom_colors["yellow"]),
            "red": tuple(custom_colors["red"]),
        }
    return None


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
        "sensor_failures": {},  # sensor_key -> {"failing_since_ms": int, "dismissed": bool}
        "sensor_alerts": [],    # computed each sensor_loop tick, see _compute_sensor_alerts
    }

    def _update_sensor_failure_tracking(sensor_key, is_failing):
        """
        Starts/clears a failure episode for one sensor. Only called for
        sensors with a genuine failure signal today (just DHT11 - see
        config.py's sensor_failure comment) - is_failing must mean "this
        reading attempt actually failed," never "this reading is None
        because the sensor isn't calibrated yet," which is a normal,
        expected state and would produce constant false-positive alerts
        if conflated with real failure.

        Recovery clears the episode ENTIRELY, including any dismissal -
        a fresh failure after a real recovery is a new episode, not a
        continuation of one the user already dismissed. See
        _handle_dismiss_sensor_alert for the dismissal-scope reasoning.
        """
        failures = latest["sensor_failures"]
        if is_failing:
            if sensor_key not in failures:
                failures[sensor_key] = {"failing_since_ms": time.ticks_ms(), "dismissed": False}
        else:
            failures.pop(sensor_key, None)

    def _compute_sensor_alerts():
        """
        Returns a list of {"sensor", "duration_s", "tier", "dismissed"}
        for every sensor that's been failing at least alert_after_s (or
        the effective, shortened threshold in "layered" mode - see
        below). "tier" is "alert" (visible in health/dashboard only) or
        "notify" (escalates to the physical LED too - see the
        sensor_loop call site below). Dismissed entries are still
        included, not hidden - dismissal silences the urgency (LED, and
        the dashboard can choose to de-emphasize it), not the underlying
        visibility that a problem exists.

        config.sensor_failure.multi_alert_mode controls what happens
        when 2+ sensors are failing at once:
        - "combined" (default): each sensor is still tracked and timed
          independently against the configured thresholds - this mode
          only changes the OUTPUT, collapsing multiple simultaneously-
          qualifying alerts into a single combined entry (sensor:
          "multiple", with the individual sensor keys listed under
          "sensors") rather than changing any timing.
        - "layered": both thresholds are divided by the number of
          CURRENTLY failing sensors (tracked or not yet past
          alert_after_s - the count includes any sensor with an open
          failure episode) before comparing, so the more things are
          wrong at once, the sooner alert/notify tiers arrive - a
          stronger, faster signal for a systemic problem (e.g. a power/
          wiring issue affecting multiple sensors at once) than treating
          each sensor as an isolated, independently-timed issue.
        """
        now = time.ticks_ms()
        cfg_sf = cfg["sensor_failure"]
        failing_count = len(latest["sensor_failures"])
        divisor = failing_count if cfg_sf["multi_alert_mode"] == "layered" and failing_count > 1 else 1
        effective_alert_after_s = cfg_sf["alert_after_s"] / divisor
        effective_notify_after_s = cfg_sf["notify_after_s"] / divisor

        qualifying = []
        for sensor_key, info in latest["sensor_failures"].items():
            duration_s = time.ticks_diff(now, info["failing_since_ms"]) / 1000
            if duration_s < effective_alert_after_s:
                continue
            tier = "notify" if duration_s >= effective_notify_after_s else "alert"
            qualifying.append({
                "sensor": sensor_key,
                "duration_s": round(duration_s),
                "tier": tier,
                "dismissed": info["dismissed"],
            })

        if not qualifying:
            return []

        if cfg_sf["multi_alert_mode"] == "combined" and len(qualifying) > 1:
            return [{
                "sensor": "multiple",
                "sensors": [q["sensor"] for q in qualifying],
                "duration_s": max(q["duration_s"] for q in qualifying),
                "tier": "notify" if any(q["tier"] == "notify" for q in qualifying) else "alert",
                # Only combined-dismissed if EVERY contributing sensor is
                # individually dismissed - one undismissed sensor inside
                # the group is still something needing attention.
                "dismissed": all(q["dismissed"] for q in qualifying),
            }]

        return qualifying

    def _handle_dismiss_sensor_alert(sensor_key):
        """
        Silences the CURRENT failure episode for one sensor - e.g. a
        user who knows their DHT11 is intentionally disconnected and
        doesn't want to keep being notified about it. Scoped to the
        ongoing episode, not permanent: if the sensor recovers and later
        fails again, that's a new episode and alerts normally (see
        _update_sensor_failure_tracking) - avoids permanently silencing
        a sensor that might fail again for an unrelated, real reason
        later. No-ops silently if the sensor isn't currently failing
        (nothing to dismiss), consistent with this project's established
        pattern of not raising just because a client's request no longer
        matches current state.
        """
        failure = latest["sensor_failures"].get(sensor_key)
        if failure is not None:
            failure["dismissed"] = True

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
        was still physically blinking red. sensor_alerts is included
        here for the same reason - a single builder used everywhere,
        rather than risking the same class of bug a second time.
        """
        payload = dict(latest["health"] or {"status": None, "reasons": []})
        payload["requires_immediate_attention"] = latest["requires_immediate_attention"]
        payload["sensor_alerts"] = latest["sensor_alerts"]
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

    def _handle_base_command(payload):
        """
        Base-level command dispatch for anything other than set_config/
        get_light_hours_today (handled internally by mqtt_client.py
        itself). Currently just dismiss_sensor_alert - a plain dict
        dispatch here rather than its own mqtt_client.py-level action
        since it's specific to main.py's own sensor-failure tracking,
        not something mqtt_client.py needs to know exists.
        """
        if payload.get("action") == "dismiss_sensor_alert":
            _handle_dismiss_sensor_alert(payload.get("sensor"))

    mqtt = mqtt_client.GreenThumbMqtt(
        cfg["mqtt"]["broker"], cfg["mqtt"]["port"], cfg,
        username=cfg["mqtt"]["username"], password=cfg["mqtt"]["password"],
        on_command=_handle_base_command,
        on_calibration_command=_handle_calibration_command,
        on_pairing_command=_handle_pairing_command,
        on_module_command=_handle_module_command,
        on_get_light_hours_today=_get_light_hours_for_mqtt,
    )

    module_mgr = module_manager.ModuleManager(ble_central, mqtt, on_module_health=None)
    pairing_mgr = pairing.PairingManager(ble_central, module_mgr, timeout_s=cfg["timing"]["pairing_timeout_s"])

    # --- Web portal providers (see web/server.py's documented contracts) ---
    def _get_dashboard_state():
        color_scheme_cfg = cfg["color_scheme"]
        return {
            "temp_f": latest["temp_f"],
            "humidity_pct": latest["humidity_pct"],
            "soil_moisture_pct": latest["soil_moisture_pct"],
            "light_fc": latest["light_fc"],
            "light_level_label": latest["light_level_label"],
            "health": latest["health"],
            "sensor_alerts": latest["sensor_alerts"],
            "wifi_ip": wifi_mgr.ip_address(),
            "mqtt_connected": mqtt.is_connected(),
            "modules": [_build_module_summary(mod_id) for mod_id in module_mgr.known_module_ids()],
            # Device-level setting, not sensor data - same precedent as
            # device_name already being exposed here (see
            # docs/ARCHITECTURE.md). Sent as the scheme NAME plus raw
            # custom_colors (not a resolved RGB dict like status_led.py
            # gets) - the dashboard is a different rendering context
            # (CSS hex, not RGB tuples) and picks its own hex
            # representation for "colorblind", kept consistent with but
            # not literally sharing code with main.py's
            # _COLORBLIND_HEALTH_COLORS constant.
            "color_scheme": {
                "web_portal": color_scheme_cfg["web_portal"],
                "custom_colors": color_scheme_cfg["custom_colors"],
            },
            "temp_unit": cfg["display"]["temp_unit"],
        }

    def _build_module_summary(mod_id):
        """
        needs_calibration/calibration_fields/name/description/
        config_required/config_options/data_sources are all provisional,
        module-defined (see docs/ARCHITECTURE.md's module development
        spec) - main.py doesn't interpret any of them, just passes
        through whatever the module's own State JSON reports, defaulting
        gracefully if the module hasn't sent any state yet
        (get_last_state() returns None right after pairing, before
        relay_forever() has connected - see that method's docstring).

        "type" is the one exception - it comes from the Device Type GATT
        characteristic read during pairing (see module_manager.register),
        not from State, so it's always available even before the module
        has sent its first State notification.
        """
        last_state = module_mgr.get_last_state(mod_id) or {}
        return {
            "mod_id": mod_id,
            "type": module_mgr.get_by_mod_id(mod_id)["type"],
            "name": last_state.get("name"),
            "description": last_state.get("description"),
            "config_required": bool(last_state.get("config_required")),
            "config_options": last_state.get("config_options", []),
            "data_sources": last_state.get("data_sources", []),
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
        dismiss_sensor_alert_handler=_handle_dismiss_sensor_alert,
    )

    # --- Display screens ---
    def _screen_temp_humidity(d):
        t, h = latest["temp_f"], latest["humidity_pct"]
        temp_val, temp_unit = _format_temp(t, cfg["display"]["temp_unit"])
        line = "{}{}  {}%".format(
            temp_val if temp_val is not None else "--",
            temp_unit,
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
            _update_sensor_failure_tracking("dht11", dht_result is None)

            soil_raw = soil.read_raw_averaged()
            soil_pct = soil.read_percent(
                soil_raw, cfg["soil_calibration"]["dry_raw"], cfg["soil_calibration"]["wet_raw"]
            )
            soil_calibrated = (
                cfg["soil_calibration"]["dry_raw"] is not None
                and cfg["soil_calibration"]["wet_raw"] is not None
            )
            # Gate on calibrated status, not just "is the reading None" -
            # an uncalibrated sensor reading None is expected/normal, not
            # a failure. Passing "soil_calibrated and soil_pct is None"
            # (rather than skipping the call outright when uncalibrated)
            # also correctly clears any previously-tracked failure if a
            # sensor transitions from calibrated back to uncalibrated
            # (e.g. mid-recalibration) - see _update_sensor_failure_tracking.
            _update_sensor_failure_tracking("soil_moisture", soil_calibrated and soil_pct is None)

            light_raw = light.read_raw_averaged()
            light_fc = light.read_fc(
                light_raw,
                cfg["light_calibration"]["dark_raw"], cfg["light_calibration"]["bright_raw"],
                cfg["light_calibration"]["bright_fc"], cfg["light_calibration"]["calibrated"],
            )
            light_calibrated = cfg["light_calibration"]["calibrated"]
            _update_sensor_failure_tracking("light", light_calibrated and light_fc is None)

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

            sensor_alerts = _compute_sensor_alerts()
            previous_sensor_alerts = latest["sensor_alerts"]
            latest["sensor_alerts"] = sensor_alerts
            led.set_alert(any(a["tier"] == "notify" and not a["dismissed"] for a in sensor_alerts))
            if sensor_alerts != previous_sensor_alerts:
                # Interrupt publish on any change (new alert, tier escalation,
                # dismissal, recovery) - same "don't make HA wait up to
                # publish_interval_s for something that just changed"
                # reasoning as the health red-transition publish above.
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
                    "light_level_label": latest["light_level_label"],
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
                health_colors=_resolve_health_colors(
                    cfg["color_scheme"]["alert_led"], cfg["color_scheme"]["custom_colors"]
                ),
                idle_mode=cfg["status_led"]["idle_mode"],
                idle_breathe_period_ms=cfg["status_led"]["idle_breathe_period_ms"],
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
                    if success:
                        # No-op if "setup" was never added (already-configured
                        # device connecting normally) - only actually removes
                        # anything when this follows a fresh setup-mode exit.
                        disp.remove_screen("setup")
                    else:
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
        ap_ssid = wifi_mgr.start_ap()

        def _screen_setup(d):
            # ap_ssid ("GreenThumb-Setup-<base_id>") is 23 chars - longer
            # than draw_screen's 16-char-per-line limit, so it's wrapped
            # across two lines rather than truncated. Truncating would cut
            # off the base_id suffix, the one part that actually tells
            # apart two Green Thumb devices both in setup mode - losing
            # exactly the information this screen exists to show.
            d.draw_screen(
                icons.ICON_WIFI, "WiFi Setup",
                ["Connect to:", ap_ssid[:16], ap_ssid[16:], "Then visit:", wifi.AP_IP],
            )

        disp.add_screen("setup", _screen_setup)

    await web.start()

    # All real work happens in the background tasks above - this just
    # keeps the coroutine (and therefore the event loop) alive.
    while True:
        await asyncio.sleep(3600)


asyncio.run(main())
