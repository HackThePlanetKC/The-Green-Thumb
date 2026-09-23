"""
web_portal.py - Minimal HTTP portal for WiFi setup and base association.

Deliberately NOT a hand-rolled asyncio HTTP server like the base
station's web/server.py - that approach exists there because
MicroPython's whole main.py runs one asyncio event loop and a vendored
framework's overhead isn't worth it on an ESP32-C3's limited RAM.
Neither constraint applies here: this module is otherwise entirely
synchronous (see wifi_manager.py, mqtt_discovery.py's background-
thread model), and Python's standard library already ships a working
HTTP server (http.server) - reimplementing HTTP parsing a second time
for a different platform would be duplicated effort with no benefit.
See docs/ARCHITECTURE.md's modules/ layout note: match the
implementation to the platform, not to the base's own patterns where
they don't translate.

Two pages only, per this task's scope - WiFi/MQTT-broker setup and
base association. Grid/settings UI is explicitly out of scope here (a
separate follow-up task, see docs/ARCHITECTURE.md).
"""

import html
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def _render(template_name, **replacements):
    path = os.path.join(_STATIC_DIR, template_name)
    with open(path) as f:
        page = f.read()
    for key, value in replacements.items():
        page = page.replace("{{" + key + "}}", value)
    return page


def make_handler(wifi_manager, discovery, association, cfg, config_module):
    """
    Returns a BaseHTTPRequestHandler subclass closed over its
    collaborators (dependency injection via closure - http.server
    instantiates handler objects internally per-request, so there's no
    constructor call site of our own to inject through, unlike
    ring.py's strip_cls pattern). See tests/test_web_portal.py, which
    exercises the route methods directly rather than over a real
    socket - no live HTTP connection needed to test the routing/
    save/render logic.

    cfg: the live config dict (as returned by config_module.load()) -
    same "mutate the shared dict, then save it" pattern the base
    station's web/server.py uses for its own settings handlers.
    """

    class PortalHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # quiet by default - a setup portal doesn't need per-request access logs

        def do_GET(self):
            if self.path in ("/", "/setup", "/associate"):
                if self.path == "/setup" or not (wifi_manager.has_credentials() and cfg["mqtt"]["broker"]):
                    self._send_html(_render("setup.html"))
                else:
                    self._send_html(self._render_associate())
            elif self.path == "/api/scan":
                self._send_json({"networks": wifi_manager.scan_networks()})
            elif self.path == "/api/bases":
                self._send_json({"bases": discovery.known_bases()})
            else:
                self.send_error(404)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode() if length else ""
            fields = {k: v[0] for k, v in parse_qs(body).items()}

            if self.path == "/save_wifi":
                self._handle_save_wifi(fields)
            elif self.path == "/save_association":
                self._handle_save_association(fields)
            else:
                self.send_error(404)

        def _handle_save_wifi(self, fields):
            """
            Saves WiFi + MQTT broker together (see config.py's mqtt
            comment for why they're bundled into one step here) and
            attempts a live connect for both, same "try it now and
            report success/failure, but save regardless" pattern the
            base station's own /save and /api/settings/mqtt handlers
            use.
            """
            ssid = fields.get("ssid", "").strip()
            password = fields.get("password", "")
            broker = fields.get("broker", "").strip()
            port_raw = fields.get("port", "1883").strip()

            if not ssid:
                self._send_json({"success": False, "error": "Network name cannot be empty"}, status=400)
                return
            try:
                port = int(port_raw) if port_raw else 1883
            except ValueError:
                self._send_json({"success": False, "error": "Port must be a number"}, status=400)
                return

            wifi_manager.set_credentials(ssid, password)
            connected = wifi_manager.connect_sta()

            cfg["wifi"]["ssid"] = ssid
            cfg["wifi"]["password"] = password
            cfg["mqtt"]["broker"] = broker
            cfg["mqtt"]["port"] = port
            config_module.save(cfg)

            discovery_started = False
            if connected and broker:
                discovery.set_broker(broker, port)
                discovery.connect()
                discovery_started = True

            self._send_json({"success": True, "connected": connected, "discovery_started": discovery_started})

        def _handle_save_association(self, fields):
            raw = fields.get("base_ids", "")
            selected = [b for b in raw.split(",") if b]
            association.set_associated_base_ids(selected)
            self._send_json({"success": True, "associated_base_ids": selected})

        def _render_associate(self):
            bases = discovery.known_bases()
            associated = set(association.associated_base_ids())
            rows = []
            for base_id, info in sorted(bases.items()):
                checked = "checked" if base_id in associated else ""
                safe_id = html.escape(base_id, quote=True)
                label = html.escape(info.get("friendly_name") or base_id)
                rows.append(
                    '<label><input type="checkbox" name="base_ids" value="{}" {}> {}</label>'.format(
                        safe_id, checked, label
                    )
                )
            rows_html = "\n".join(rows) if rows else "<p>No bases discovered yet.</p>"
            return _render("associate.html", rows=rows_html)

        def _send_html(self, body):
            encoded = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_json(self, data, status=200):
            encoded = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    return PortalHandler


def serve_forever(wifi_manager, discovery, association, cfg, config_module, port=80):
    handler_cls = make_handler(wifi_manager, discovery, association, cfg, config_module)
    server = ThreadingHTTPServer(("0.0.0.0", port), handler_cls)
    server.serve_forever()
