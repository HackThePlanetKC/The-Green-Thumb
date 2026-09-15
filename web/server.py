"""
web/server.py - Minimal HTTP server for the initial-setup web portal
(served in AP mode - see core/wifi.py) and the standalone dashboard for
non-HA users (see docs/ARCHITECTURE.md).

This is NOT a general-purpose HTTP server - it's deliberately narrow:
GET for pages/static assets, POST with a bounded
application/x-www-form-urlencoded body (max _MAX_BODY_BYTES). No
chunked transfer encoding, no HTTPS (unrealistic/unnecessary for a
local AP setup flow), no authentication (trusted home LAN, matching
most local IoT devices - see docs/ARCHITECTURE.md). Uses
asyncio.start_server (a standard MicroPython asyncio primitive) rather
than a vendored web framework - the page count here is small enough
that a framework's routing/middleware overhead isn't worth it on an
ESP32-C3's limited RAM.

Routing for "/": if WiFi credentials exist (wifi_manager.has_credentials()),
serves the dashboard - even if the connection is currently down, so a
temporary WiFi hiccup doesn't bounce a configured device back to
onboarding. Only shows the setup form if no credentials have ever been
saved.

DATA CONTRACT (main.py doesn't exist yet - built against this
documented shape, same pattern as core/pairing.py's ble_central/
module_manager interfaces): state_provider is a zero-argument callable
passed to WebServer, returning a dict shaped like:

    {
        "temp_f": float or None,
        "humidity_pct": float or None,
        "soil_moisture_pct": float or None,
        "light_fc": float or None,
        "light_level_label": str or None,
        "health": {"status": "green"|"yellow"|"red"|None, "reasons": [...]},
        "wifi_ip": str or None,
        "mqtt_connected": bool,
        "modules": [{"mod_id": str, "type": str, "online": bool}, ...],
    }

"modules" is optional in the returned dict - defaults to [] if absent,
so a state_provider written before module_manager.py existed doesn't
need updating just to keep working. "light_level_label" is also
optional (defaults to None if absent) - only meaningful when
config.light_mode.mode == "multi_point" (see drivers/light_sensor.py's
classify_light_level); the dashboard only shows this element when it's
non-null, so a provider that doesn't populate it (single-fc mode, or
not written yet) just doesn't show the element rather than showing a
blank one. "device_name" is NOT part of this contract - it's read
directly from config by the server itself (a device setting, not
sensor data), so it's always available even before
main.py/module_manager.py exist.

This mirrors the same field set already shown on the OLED display
(drivers/display.py's core screens) and the MQTT state/health topics,
so the same underlying readings are consistent across all three
surfaces. None values (sensor unavailable/uncalibrated) are rendered
as "—" by the dashboard's JS, not a fabricated number. Note: light_fc
remains part of this contract (other consumers - OLED, MQTT - still
use it), but the dashboard itself no longer displays it directly - see
light_hours_provider below for what replaced it there.

SECOND CONTRACT - light_hours_provider: a separate zero-argument
callable, returning today's light_tracker.get_light_hours_today() value
(float hours) or None. Kept entirely separate from state_provider
because the dashboard treats this value as static-on-load rather than
part of the live 5s-polled readings (see GET /api/light_hours_today) -
folding it into state_provider would have coupled its refresh cadence
to the other readings', which is exactly what the static-on-load
design avoids.

Settings/calibration/pairing pages are a later addition to this same
server.
"""

import asyncio

try:
    import ujson as json
except ImportError:
    import json

import config as config_module

_STATIC_DIR = "/web/static"
_MAX_BODY_BYTES = 2048
_MAX_HEADER_LINES = 32

_CONTENT_TYPES = {
    ".html": "text/html",
    ".css": "text/css",
    ".json": "application/json",
}

_STATUS_TEXT = {
    200: "OK",
    400: "Bad Request",
    404: "Not Found",
    405: "Method Not Allowed",
    503: "Service Unavailable",
}

# Settings page field mapping: (form field name, config path, type, nullable).
# Reuses config.get_by_path/set_by_path directly - the exact same
# validated path-setter the MQTT set_config command uses (see
# core/mqtt_client.py) - so the settings page is a thin form<->config
# layer, not a second implementation of config validation.
#
# light_tracking.high_intensity_hours_target IS included (min and max) -
# an earlier version of this schema had it defined but never actually
# consumed by light_tracker.py, so it was excluded here as dead config.
# That's now fixed (light_tracker.py evaluates it properly, gated on
# thresholds.light_fc.red_max also being set - see that module's
# docstring), so it belongs here as a real, working, optional field.
#
# Also excluded: raw calibration values (soil_calibration.dry_raw/wet_raw,
# light_calibration.dark_raw/bright_raw/bright_fc). These are only
# meaningful as output of the guided calibration procedure (which
# validates the two readings differ enough to matter - see
# core/calibration.py) - editing them as bare numbers here would bypass
# that validation entirely. Settings shows read-only calibration status;
# actual (re)calibration happens on the dedicated calibration page.
# light_mode.points (multi-point light level samples) has the same
# reasoning - handled via dedicated add/remove/sample endpoints below,
# not this generic field list, since raw values there should come from a
# live sample too, not be hand-typed.
_SETTINGS_FIELDS = [
    ("temp_green_min", "thresholds.temp_f.green_min", "float", False),
    ("temp_green_max", "thresholds.temp_f.green_max", "float", False),
    ("temp_red_min", "thresholds.temp_f.red_min", "float", True),
    ("temp_red_max", "thresholds.temp_f.red_max", "float", True),
    ("humidity_green_min", "thresholds.humidity_pct.green_min", "float", False),
    ("humidity_green_max", "thresholds.humidity_pct.green_max", "float", False),
    ("humidity_red_min", "thresholds.humidity_pct.red_min", "float", True),
    ("humidity_red_max", "thresholds.humidity_pct.red_max", "float", True),
    ("soil_green_min", "thresholds.soil_moisture_pct.green_min", "float", False),
    ("soil_green_max", "thresholds.soil_moisture_pct.green_max", "float", False),
    ("soil_red_min", "thresholds.soil_moisture_pct.red_min", "float", True),
    ("soil_red_max", "thresholds.soil_moisture_pct.red_max", "float", True),
    ("light_green_min", "thresholds.light_fc.green_min", "float", False),
    ("light_green_max", "thresholds.light_fc.green_max", "float", False),
    ("light_red_min", "thresholds.light_fc.red_min", "float", True),
    ("light_red_max", "thresholds.light_fc.red_max", "float", True),
    ("light_present_threshold_fc", "light_tracking.present_threshold_fc", "float", False),
    ("light_hours_target_min", "light_tracking.hours_target.min", "float", False),
    ("light_hours_target_max", "light_tracking.hours_target.max", "float", False),
    # Optional/non-essential - see core/light_tracker.py. Only takes
    # effect once thresholds.light_fc.red_max is also set.
    ("light_high_intensity_target_min", "light_tracking.high_intensity_hours_target.min", "float", True),
    ("light_high_intensity_target_max", "light_tracking.high_intensity_hours_target.max", "float", True),
    ("sample_interval_s", "timing.sample_interval_s", "int", False),
    ("publish_interval_s", "timing.publish_interval_s", "int", False),
    ("pairing_timeout_s", "timing.pairing_timeout_s", "int", False),
    ("auto_cycle_interval_s", "display.auto_cycle_interval_s", "int", False),
    ("resume_idle_s", "display.resume_idle_s", "int", False),
    ("night_mode_enabled", "display.night_mode.enabled", "bool", False),
    ("night_mode_start_hour", "display.night_mode.start_hour", "int", False),
    ("night_mode_start_minute", "display.night_mode.start_minute", "int", False),
    ("night_mode_end_hour", "display.night_mode.end_hour", "int", False),
    ("night_mode_end_minute", "display.night_mode.end_minute", "int", False),
    ("night_mode_led_off", "display.night_mode.led_off", "bool", False),
    ("night_mode_red_overrides_led_off", "display.night_mode.red_overrides_led_off", "bool", False),
    ("led_brightness", "status_led.brightness", "float", False),
    ("led_blink_interval_ms", "status_led.blink_interval_ms", "int", False),
    ("led_red_escalation_delay_s", "status_led.red_escalation_delay_s", "int", False),
    ("utc_offset_hours", "timezone.utc_offset_hours", "float", False),
]


def url_decode(s):
    """
    Decodes an application/x-www-form-urlencoded string: '+' -> space,
    '%XX' -> byte. Operates on a byte buffer throughout (not per-
    character chr() substitution) so multi-byte UTF-8 sequences - e.g.
    an SSID with an accented character - decode correctly instead of
    producing mojibake. Malformed percent-sequences (truncated at the
    end of the string, or non-hex digits) are left as literal bytes
    rather than raising. Assumes the input is already ASCII-safe wire
    format with non-ASCII characters escaped as %XX (which is how
    application/x-www-form-urlencoded data actually arrives from a
    browser - non-ASCII is never sent as literal bytes in this format).
    """
    result = bytearray()
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == "+":
            result.append(0x20)
            i += 1
        elif c == "%" and i + 2 < n:
            try:
                result.append(int(s[i + 1:i + 3], 16))
                i += 3
            except ValueError:
                result.extend(c.encode())
                i += 1
        else:
            result.extend(c.encode())
            i += 1
    try:
        return bytes(result).decode("utf-8")
    except UnicodeDecodeError:
        # Fully malformed byte sequence (e.g. a stray continuation byte
        # with no valid multi-byte sequence around it) - fall back to a
        # lossy byte-per-character decode rather than crashing the
        # request handler over a malformed client input. chr() never
        # raises for any 0-255 byte value, so this is always safe, even
        # though the result may look garbled for the malformed portion.
        return "".join(chr(b) for b in result)


def parse_form_body(body_str):
    """Parses 'a=1&b=2' into {'a': '1', 'b': '2'}, URL-decoding keys/values."""
    fields = {}
    if not body_str:
        return fields
    for pair in body_str.split("&"):
        if "=" in pair:
            key, value = pair.split("=", 1)
        else:
            key, value = pair, ""
        fields[url_decode(key)] = url_decode(value)
    return fields


def parse_request_line(line):
    """Parses 'GET /path HTTP/1.1' -> (method, path). Returns (None, None) if malformed."""
    parts = line.strip().split(" ")
    if len(parts) < 2:
        return None, None
    return parts[0], parts[1]


class WebServer:
    def __init__(
        self, cfg, wifi_manager, state_provider=None, light_hours_provider=None,
        mqtt_manager=None, light_raw_sample_provider=None,
        calibration_manager=None, calibration_command_handler=None,
        pairing_manager=None, pairing_command_handler=None,
        module_manager=None,
        port=80,
    ):
        self._cfg = cfg
        self._wifi_manager = wifi_manager
        self._state_provider = state_provider
        self._light_hours_provider = light_hours_provider
        self._mqtt_manager = mqtt_manager
        self._light_raw_sample_provider = light_raw_sample_provider
        self._calibration_manager = calibration_manager
        self._calibration_command_handler = calibration_command_handler
        self._pairing_manager = pairing_manager
        self._pairing_command_handler = pairing_command_handler
        self._module_manager = module_manager
        self._port = port
        self._server = None

    async def start(self):
        self._server = await asyncio.start_server(self._handle_client, "0.0.0.0", self._port)

    async def _handle_client(self, reader, writer):
        try:
            request_line = await reader.readline()
            if not request_line:
                return
            method, path = parse_request_line(request_line.decode())
            if method is None:
                await self._send_response(writer, 400, "text/plain", b"Bad Request")
                return

            headers = await self._read_headers(reader)

            if method == "GET":
                await self._handle_get(writer, path)
            elif method == "POST":
                body = await self._read_body(reader, headers)
                await self._handle_post(writer, path, body)
            else:
                await self._send_response(writer, 405, "text/plain", b"Method Not Allowed")
        finally:
            writer.close()

    async def _read_headers(self, reader):
        headers = {}
        for _ in range(_MAX_HEADER_LINES):
            line = await reader.readline()
            if not line or line in (b"\r\n", b"\n", b""):
                break
            decoded = line.decode().strip()
            if ":" in decoded:
                key, value = decoded.split(":", 1)
                headers[key.strip().lower()] = value.strip()
        return headers

    async def _read_body(self, reader, headers):
        try:
            length = int(headers.get("content-length", 0))
        except ValueError:
            length = 0
        length = max(0, min(length, _MAX_BODY_BYTES))
        if length == 0:
            return ""
        try:
            data = await reader.readexactly(length)
        except EOFError:
            # Client declared a Content-Length it didn't actually send
            # (connection dropped mid-body, or a malformed request).
            # asyncio.IncompleteReadError (CPython) is itself a subclass
            # of EOFError, so this also covers that case without needing
            # a separate except clause. Returning "" here rather than
            # letting this propagate means downstream field validation
            # (e.g. "ssid cannot be empty") naturally produces the
            # correct 400 response - an uncaught exception here would
            # instead skip straight to _handle_client's finally block
            # and close the connection with no response sent at all.
            return ""
        return data.decode()

    async def _handle_get(self, writer, path):
        if path == "/":
            if self._wifi_manager.has_credentials():
                await self._serve_static(writer, "/dashboard.html")
            else:
                await self._serve_static(writer, "/setup.html")
        elif path == "/settings":
            await self._serve_static(writer, "/settings.html")
        elif path == "/calibration":
            await self._serve_static(writer, "/calibration.html")
        elif path == "/pairing":
            await self._serve_static(writer, "/pairing.html")
        elif path == "/style.css":
            await self._serve_static(writer, "/style.css")
        elif path == "/api/state":
            await self._handle_api_state(writer)
        elif path == "/api/light_hours_today":
            await self._handle_light_hours_today(writer)
        elif path == "/api/settings":
            await self._handle_get_settings(writer)
        elif path == "/api/settings/sample_light":
            await self._handle_sample_light(writer)
        elif path == "/api/calibration/status":
            await self._handle_get_calibration_status(writer)
        elif path == "/api/pairing/status":
            await self._handle_get_pairing_status(writer)
        else:
            await self._send_response(writer, 404, "text/plain", b"Not Found")

    async def _handle_api_state(self, writer):
        """
        Returns the current sensor/health snapshot as JSON - see the
        DATA CONTRACT in the module docstring. Returns a default
        all-None/disconnected shape if no state_provider was supplied
        (e.g. this server instance is only being used for the setup
        flow) rather than raising - a dashboard fetch hitting an
        AP-mode-only server should get an empty-but-valid response, not
        a 500. device_name always comes from config directly, regardless
        of whether a state_provider exists.
        """
        if self._state_provider is None:
            data = {
                "temp_f": None, "humidity_pct": None,
                "soil_moisture_pct": None, "light_fc": None,
                "light_level_label": None,
                "health": {"status": None, "reasons": []},
                "wifi_ip": None, "mqtt_connected": False,
                "modules": [],
            }
        else:
            data = self._state_provider()
            data.setdefault("modules", [])
            data.setdefault("light_level_label", None)
        data["device_name"] = self._cfg.get("device_name", "Green Thumb")
        await self._send_response(writer, 200, "application/json", json.dumps(data).encode())

    async def _handle_light_hours_today(self, writer):
        """
        Separate endpoint (not part of /api/state) specifically because
        the dashboard treats this value as static-on-load rather than
        part of the live 5s-polled readings - fetched once when the page
        loads and again only on an explicit manual refresh, not on every
        poll tick. light_hours_provider is a zero-argument callable
        returning {"light_hours_today": float or None} - matches
        core/light_tracker.py's get_light_hours_today(), with target
        range included from config directly (not the provider) since
        that's a config value, not something requiring live sensor state.
        """
        target = self._cfg.get("light_tracking", {}).get("hours_target", {})
        if self._light_hours_provider is None:
            hours = None
        else:
            hours = self._light_hours_provider()
        data = {
            "light_hours_today": hours,
            "target_min": target.get("min"),
            "target_max": target.get("max"),
        }
        await self._send_response(writer, 200, "application/json", json.dumps(data).encode())

    async def _serve_static(self, writer, rel_path):
        full_path = _STATIC_DIR + rel_path
        dot = rel_path.rfind(".")
        ext = rel_path[dot:] if dot != -1 else ""
        content_type = _CONTENT_TYPES.get(ext, "application/octet-stream")
        try:
            with open(full_path, "rb") as f:
                data = f.read()
            await self._send_response(writer, 200, content_type, data)
        except OSError:
            await self._send_response(writer, 404, "text/plain", b"Not Found")

    async def _handle_post(self, writer, path, body):
        if path == "/save":
            await self._handle_save_wifi(writer, body)
        elif path == "/api/set_name":
            await self._handle_set_name(writer, body)
        elif path == "/api/settings/save":
            await self._handle_settings_save(writer, body)
        elif path == "/api/settings/mqtt":
            await self._handle_settings_mqtt(writer, body)
        elif path == "/api/settings/light_mode":
            await self._handle_settings_light_mode(writer, body)
        elif path == "/api/settings/light_points/add":
            await self._handle_light_points_add(writer, body)
        elif path == "/api/settings/light_points/remove":
            await self._handle_light_points_remove(writer, body)
        elif path == "/api/calibration/command":
            await self._handle_calibration_command_route(writer, body)
        elif path == "/api/pairing/command":
            await self._handle_pairing_command_route(writer, body)
        elif path == "/api/modules/calibrate":
            await self._handle_modules_calibrate(writer, body)
        else:
            await self._send_response(writer, 404, "text/plain", b"Not Found")

    async def _handle_set_name(self, writer, body):
        """
        Renames the device from the dashboard. Bounded to 40 characters -
        this name may eventually also show on the OLED display (a 16-
        char-wide, 128px display), so an unbounded name could break
        layout somewhere down the line even though it fits fine in a
        web page today.
        """
        fields = parse_form_body(body)
        name = fields.get("name", "").strip()

        if not name:
            error_body = json.dumps({"success": False, "error": "Name cannot be empty"}).encode()
            await self._send_response(writer, 400, "application/json", error_body)
            return

        name = name[:40]
        self._cfg["device_name"] = name
        config_module.save(self._cfg)
        await self._send_response(
            writer, 200, "application/json",
            json.dumps({"success": True, "device_name": name}).encode(),
        )

    async def _handle_save_wifi(self, writer, body):
        fields = parse_form_body(body)
        ssid = fields.get("ssid", "").strip()
        password = fields.get("password", "")

        if not ssid:
            html = self._render_result_page(False, "Network name cannot be empty.")
            await self._send_response(writer, 400, "text/html", html.encode())
            return

        self._cfg["wifi"]["ssid"] = ssid
        self._cfg["wifi"]["password"] = password
        config_module.save(self._cfg)

        self._wifi_manager.set_credentials(ssid, password)
        connected = await self._wifi_manager.connect_sta()

        if connected:
            ip = self._wifi_manager.ip_address()
            html = self._render_result_page(True, "Connected! Device IP: {}".format(ip))
        else:
            html = self._render_result_page(
                False, "Could not connect with those credentials. Check them and try again."
            )

        await self._send_response(writer, 200, "text/html", html.encode())

    def _render_result_page(self, success, message):
        try:
            with open(_STATIC_DIR + "/result.html") as f:
                template = f.read()
        except OSError:
            template = "<html><body>{{message}}</body></html>"
        status_class = "success" if success else "error"
        return template.replace("{{message}}", message).replace("{{status_class}}", status_class)

    async def _handle_get_settings(self, writer):
        """
        Returns current values for every field in _SETTINGS_FIELDS,
        plus read-only calibration status and MQTT broker info (with
        the password masked - returned as a boolean "is one set",
        never the actual value, even though this project otherwise
        accepts a trusted-LAN/no-auth model; echoing a password back in
        cleartext is a separate, easy-to-avoid habit worth avoiding
        regardless).
        """
        values = {}
        for field_name, path, _type, _nullable in _SETTINGS_FIELDS:
            try:
                values[field_name] = config_module.get_by_path(self._cfg, path)
            except KeyError:
                values[field_name] = None  # schema drift safety net - shouldn't happen, but never 500 over it

        soil_cal = self._cfg.get("soil_calibration", {})
        light_cal = self._cfg.get("light_calibration", {})
        mqtt_cfg = self._cfg.get("mqtt", {})
        light_mode_cfg = self._cfg.get("light_mode", {})

        data = {
            "fields": values,
            "calibration": {
                "soil_calibrated": soil_cal.get("dry_raw") is not None and soil_cal.get("wet_raw") is not None,
                "light_calibrated": bool(light_cal.get("calibrated")),
            },
            "light_mode": {
                "mode": light_mode_cfg.get("mode", "single"),
                "points": light_mode_cfg.get("points", []),
            },
            "mqtt": {
                "broker": mqtt_cfg.get("broker", ""),
                "port": mqtt_cfg.get("port", 1883),
                "username": mqtt_cfg.get("username", ""),
                "password_set": bool(mqtt_cfg.get("password")),
                "connected": self._mqtt_manager.is_connected() if self._mqtt_manager else False,
            },
        }
        await self._send_response(writer, 200, "application/json", json.dumps(data).encode())

    async def _handle_settings_save(self, writer, body):
        """
        Applies every recognized field from _SETTINGS_FIELDS found in
        the submitted form body, using config.set_by_path (the same
        validated path-setter the MQTT set_config command uses) for
        each one, then saves once at the end - not once per field, to
        avoid redundant flash writes for a form submitting 30+ fields
        at once.

        Unrecognized form fields are ignored silently (not an error) -
        this keeps the handler forward-compatible with a settings page
        that might submit extra fields (e.g. a future addition) without
        needing this handler updated in lockstep, as long as the new
        field is registered in _SETTINGS_FIELDS.

        A field that fails type coercion (e.g. non-numeric text in a
        number field) is skipped individually rather than aborting the
        whole save - consistent with this project's "no ack channel,
        reject silently" pattern elsewhere (see core/mqtt_client.py's
        set_config), but scoped per-field here so one bad value doesn't
        also block 29 good ones in the same submission.
        """
        fields = parse_form_body(body)
        applied = []
        skipped = []

        for field_name, path, field_type, nullable in _SETTINGS_FIELDS:
            if field_name not in fields:
                continue
            raw_value = fields[field_name]

            if field_type == "bool":
                value = raw_value in ("true", "1", "on", "yes")
            elif raw_value == "" and nullable:
                value = None
            else:
                try:
                    value = float(raw_value) if field_type == "float" else int(raw_value)
                except ValueError:
                    skipped.append(field_name)
                    continue

            try:
                config_module.set_by_path(self._cfg, path, value)
                applied.append(field_name)
            except KeyError:
                skipped.append(field_name)  # schema drift safety net

        if applied:
            config_module.save(self._cfg)

        result = {"success": True, "applied": applied, "skipped": skipped}
        await self._send_response(writer, 200, "application/json", json.dumps(result).encode())

    async def _handle_settings_mqtt(self, writer, body):
        """
        Saves new MQTT broker settings and attempts a live reconnect,
        same "try it now and report success/failure" UX as the WiFi
        setup flow's /save handler. An empty password field means
        "leave the existing password unchanged" (so re-saving broker/
        port without re-typing a password you don't want to change
        doesn't wipe it) - only a non-empty password field overwrites
        the stored one.
        """
        fields = parse_form_body(body)
        broker = fields.get("broker", "").strip()
        port_raw = fields.get("port", "1883").strip()
        username = fields.get("username", "").strip()
        password = fields.get("password", "")

        if not broker:
            result = {"success": False, "error": "Broker address cannot be empty"}
            await self._send_response(writer, 400, "application/json", json.dumps(result).encode())
            return

        try:
            port = int(port_raw)
        except ValueError:
            result = {"success": False, "error": "Port must be a number"}
            await self._send_response(writer, 400, "application/json", json.dumps(result).encode())
            return

        if not password:
            password = self._cfg.get("mqtt", {}).get("password", "")

        self._cfg["mqtt"]["broker"] = broker
        self._cfg["mqtt"]["port"] = port
        self._cfg["mqtt"]["username"] = username
        self._cfg["mqtt"]["password"] = password
        config_module.save(self._cfg)

        connected = False
        if self._mqtt_manager is not None:
            self._mqtt_manager.set_broker(broker, port, username, password)
            try:
                await self._mqtt_manager.connect()
                connected = True
            except OSError:
                connected = False

        result = {"success": True, "connected": connected}
        await self._send_response(writer, 200, "application/json", json.dumps(result).encode())

    async def _handle_sample_light(self, writer):
        """
        Returns a fresh raw light sensor reading on demand, via
        light_raw_sample_provider (zero-argument callable, documented
        contract ahead of main.py existing - same pattern as
        state_provider/light_hours_provider). Used by the settings
        page's "Sample Now" button when defining a multi-point light
        level reference (see drivers/light_sensor.py's
        classify_light_level) - raw values should come from a live
        sample of the actual sensor sitting in that lighting condition,
        not be hand-typed, for the same reason calibration.py's flow
        doesn't accept hand-typed calibration values either.
        """
        if self._light_raw_sample_provider is None:
            data = {"raw": None}
        else:
            data = {"raw": self._light_raw_sample_provider()}
        await self._send_response(writer, 200, "application/json", json.dumps(data).encode())

    async def _handle_settings_light_mode(self, writer, body):
        """
        Switches between "single" (continuous fc via 2-point dark/bright
        calibration) and "multi_point" (named reference points, nearest-
        match classification) light sensor modes. A dedicated endpoint
        rather than a _SETTINGS_FIELDS entry because the value needs
        validation against a fixed allowed set, which the generic
        float/int/bool coercion in _handle_settings_save doesn't support.
        """
        fields = parse_form_body(body)
        mode = fields.get("mode", "").strip()

        if mode not in ("single", "multi_point"):
            result = {"success": False, "error": "mode must be 'single' or 'multi_point'"}
            await self._send_response(writer, 400, "application/json", json.dumps(result).encode())
            return

        self._cfg["light_mode"]["mode"] = mode
        config_module.save(self._cfg)
        result = {"success": True, "mode": mode}
        await self._send_response(writer, 200, "application/json", json.dumps(result).encode())

    async def _handle_light_points_add(self, writer, body):
        """
        Appends a new named reference point (see
        drivers/light_sensor.py's classify_light_level). Expects both a
        label and a raw value - the raw value is expected to come from
        the "Sample Now" flow (GET /api/settings/sample_light), not be
        hand-typed, though this handler itself doesn't distinguish where
        the number came from (the UI is responsible for guiding the
        user through sampling rather than typing).
        """
        fields = parse_form_body(body)
        label = fields.get("label", "").strip()
        raw_str = fields.get("raw", "").strip()

        if not label:
            result = {"success": False, "error": "Label cannot be empty"}
            await self._send_response(writer, 400, "application/json", json.dumps(result).encode())
            return

        try:
            raw = int(raw_str)
        except ValueError:
            result = {"success": False, "error": "Raw value must be a number"}
            await self._send_response(writer, 400, "application/json", json.dumps(result).encode())
            return

        label = label[:40]  # same bounding reasoning as device_name - see _handle_set_name
        self._cfg["light_mode"]["points"].append({"label": label, "raw": raw})
        config_module.save(self._cfg)

        result = {"success": True, "points": self._cfg["light_mode"]["points"]}
        await self._send_response(writer, 200, "application/json", json.dumps(result).encode())

    async def _handle_light_points_remove(self, writer, body):
        """Removes a point by its index in the current list."""
        fields = parse_form_body(body)
        index_str = fields.get("index", "").strip()

        try:
            index = int(index_str)
        except ValueError:
            result = {"success": False, "error": "index must be a number"}
            await self._send_response(writer, 400, "application/json", json.dumps(result).encode())
            return

        points = self._cfg["light_mode"]["points"]
        if index < 0 or index >= len(points):
            result = {"success": False, "error": "index out of range"}
            await self._send_response(writer, 400, "application/json", json.dumps(result).encode())
            return

        points.pop(index)
        config_module.save(self._cfg)
        result = {"success": True, "points": points}
        await self._send_response(writer, 200, "application/json", json.dumps(result).encode())

    async def _handle_get_calibration_status(self, writer):
        """
        Returns core/calibration.py's current status - the SAME state
        machine MQTT drives, not a separate copy. Returns a safe idle
        default if no calibration_manager was supplied (e.g. this server
        instance is only being used for the setup flow).
        """
        if self._calibration_manager is None:
            data = {"state": "idle", "target": None, "first_raw": None, "second_raw": None, "error": None}
        else:
            data = self._calibration_manager.status()
        await self._send_response(writer, 200, "application/json", json.dumps(data).encode())

    async def _handle_calibration_command_route(self, writer, body):
        """
        Builds a payload dict from the submitted form fields and hands
        it to calibration_command_handler - the exact same function
        main.py passes to mqtt_client's on_calibration_command, so a
        web-triggered calibration step goes through the identical code
        path (including the MQTT status republish that function already
        does) as one triggered from Home Assistant. This route does not
        reimplement any calibration logic itself.
        """
        fields = parse_form_body(body)
        payload = {"action": fields.get("action")}
        if "target" in fields:
            payload["target"] = fields["target"]
        if "bright_fc" in fields and fields["bright_fc"] != "":
            try:
                payload["bright_fc"] = float(fields["bright_fc"])
            except ValueError:
                pass  # let calibration.py's own validation reject a missing/invalid bright_fc

        if self._calibration_command_handler is not None:
            self._calibration_command_handler(payload)

        status = self._calibration_manager.status() if self._calibration_manager else {}
        await self._send_response(writer, 200, "application/json", json.dumps(status).encode())

    async def _handle_get_pairing_status(self, writer):
        """Returns core/pairing.py's current status - see _handle_get_calibration_status for the same reasoning."""
        if self._pairing_manager is None:
            data = {"state": "idle"}
        else:
            data = self._pairing_manager.status()
        await self._send_response(writer, 200, "application/json", json.dumps(data).encode())

    async def _handle_pairing_command_route(self, writer, body):
        """
        Same reuse pattern as calibration above - pairing_command_handler
        is the identical function main.py passes to mqtt_client's
        on_pairing_command, so this route doesn't reimplement pairing
        logic, LED updates, or MQTT status publishing itself.
        """
        fields = parse_form_body(body)
        payload = {"action": fields.get("action")}
        if "address" in fields:
            payload["address"] = fields["address"]

        if self._pairing_command_handler is not None:
            self._pairing_command_handler(payload)

        status = self._pairing_manager.status() if self._pairing_manager else {}
        await self._send_response(writer, 200, "application/json", json.dumps(status).encode())

    async def _handle_modules_calibrate(self, writer, body):
        """
        Generic passthrough for sending calibration values to ANY paired
        module - the calibration page is deliberately not aware of any
        specific module type's calibration schema (see
        docs/ARCHITECTURE.md's provisional module calibration contract).
        Form fields prefixed "cal_" become the "values" dict sent to the
        module via Command as {"action": "set_calibration", "values": {...}} -
        this handler does not interpret or validate individual field
        names/values itself, since it has no way to know what a given
        module type actually expects. mod_id is required and looked up
        directly, not path-parameterized (this server's router is
        deliberately narrow - see module docstring - exact-match only,
        no path parameters), so mod_id travels as a form field instead.
        """
        fields = parse_form_body(body)
        mod_id = fields.get("mod_id", "").strip()

        if not mod_id:
            result = {"success": False, "error": "mod_id is required"}
            await self._send_response(writer, 400, "application/json", json.dumps(result).encode())
            return

        if self._module_manager is None:
            result = {"success": False, "error": "module support not available"}
            await self._send_response(writer, 503, "application/json", json.dumps(result).encode())
            return

        values = {}
        for key, value in fields.items():
            if key.startswith("cal_"):
                values[key[4:]] = value

        payload = {"action": "set_calibration", "values": values}
        success = await self._module_manager.send_command(mod_id, payload)

        result = {"success": success}
        if not success:
            result["error"] = "module not currently connected"
        await self._send_response(writer, 200, "application/json", json.dumps(result).encode())

    async def _send_response(self, writer, status, content_type, body_bytes):
        status_text = _STATUS_TEXT.get(status, "")
        header = (
            "HTTP/1.1 {} {}\r\n"
            "Content-Type: {}\r\n"
            "Content-Length: {}\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).format(status, status_text, content_type, len(body_bytes))
        writer.write(header.encode())
        writer.write(body_bytes)
        await writer.drain()
