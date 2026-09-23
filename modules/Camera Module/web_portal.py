"""
web_portal.py - HTTP portal: WiFi/broker setup, base association, grid
layout, and settings (module-global + per-base).

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

Routing flow: setup -> associate -> a small dashboard linking to grid
layout and settings (the last two are ongoing-editable pages, not one-
shot steps, so "/" stops advancing through them automatically once at
least one base is associated).

Settings scope (see per_base_settings.py / grid_config.py): this
portal can edit module-global settings AND any associated base's
per-base metric toggles - the "a base can only edit its own settings"
restriction (item 6/7 of the grid/settings task) applies to a BASE's
own portal, which is base firmware, not this file (see
decisions-and-practices.md for why that side isn't built here).
"""

import html
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

from camera_capture import capture_still as _real_capture_still
from image_compare import load_grayscale_from_file as _real_load_image_from_file
from visual_disclaimers import FULL_DISCLAIMER

_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_GRID_REFERENCE_PATH = os.path.join(_DATA_DIR, "grid_reference.jpg")
_WILT_CAPTURE_TMP_PATH = os.path.join(_DATA_DIR, "wilt_capture_tmp.jpg")


def _safe_json_for_script(data):
    """
    json.dumps() output embedded directly into an inline <script> block
    (grid.html/settings.html's bases/cells/per-base data, all sourced
    from other devices' user-editable friendly_name fields over MQTT -
    see mqtt_discovery.py). json.dumps doesn't escape "/", so a
    friendly_name containing the literal text "</script>" could
    otherwise break out of the script block - same class of concern
    _render_associate's html.escape() already guards against for HTML-
    context rendering, applied here for JS-context embedding instead.
    """
    return json.dumps(data).replace("</", "<\\/")


def _render(template_name, **replacements):
    path = os.path.join(_STATIC_DIR, template_name)
    with open(path) as f:
        page = f.read()
    for key, value in replacements.items():
        page = page.replace("{{" + key + "}}", value)
    return page


def make_handler(
    wifi_manager, discovery, association, config_module,
    grid_config, per_base_settings, wilt_watch, presence,
    capture_still=_real_capture_still, load_image_from_file=_real_load_image_from_file,
):
    """
    Returns a BaseHTTPRequestHandler subclass closed over its
    collaborators (dependency injection via closure - http.server
    instantiates handler objects internally per-request, so there's no
    constructor call site of our own to inject through, unlike
    ring.py's strip_cls pattern). See tests/test_web_portal.py, which
    exercises the route methods directly rather than over a real
    socket - no live HTTP connection needed to test the routing/
    save/render logic.

    No long-lived shared config dict is held here - every handler does
    its own config_module.load() immediately before reading/mutating,
    and config_module.save() immediately after, same pattern
    grid_config.py/per_base_settings.py/base_association.py already
    use. An earlier version of this file held one `cfg` dict loaded
    once at startup and mutated in place for wifi/mqtt settings only -
    that's a real staleness hazard once other managers (grid_config,
    per_base_settings) also read/write config.json independently: a
    save from the long-held stale dict could silently clobber
    assignments a fresh load()/save() cycle had written in the
    meantime. Fixed by dropping the shared dict entirely in favor of
    one consistent load-mutate-save pattern everywhere.

    capture_still/load_image_from_file are injected specifically so
    tests never need a real camera or Pillow installed - see
    camera_capture.py and image_compare.py's own module docstrings for
    why each is guarded/injectable in the first place.
    """

    class PortalHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # quiet by default - a setup portal doesn't need per-request access logs

        def do_GET(self):
            if self.path in ("/", "/setup", "/associate"):
                self._route_root()
            elif self.path == "/grid":
                self._send_html(self._render_grid())
            elif self.path == "/settings":
                self._send_html(self._render_settings())
            elif self.path == "/api/scan":
                self._send_json({"networks": wifi_manager.scan_networks()})
            elif self.path == "/api/bases":
                self._send_json({"bases": discovery.known_bases()})
            elif self.path == "/api/associated_bases":
                self._send_json({"bases": self._associated_bases_with_names()})
            elif self.path == "/api/grid/reference.jpg":
                self._send_grid_reference()
            else:
                self.send_error(404)

        def _route_root(self):
            current = config_module.load()
            if self.path == "/setup" or not (wifi_manager.has_credentials() and current["mqtt"]["broker"]):
                self._send_html(_render("setup.html"))
            elif self.path == "/associate" or not association.associated_base_ids():
                self._send_html(self._render_associate())
            else:
                self._send_html(_render("dashboard.html"))

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode() if length else ""
            fields = {k: v[0] for k, v in parse_qs(body).items()}

            handlers = {
                "/save_wifi": self._handle_save_wifi,
                "/save_association": self._handle_save_association,
                "/save_grid": self._handle_save_grid,
                "/save_global_settings": self._handle_save_global_settings,
                "/save_per_base_metric": self._handle_save_per_base_metric,
                "/api/grid/capture_reference": self._handle_capture_grid_reference,
                "/api/wilt_watch/capture_reference": self._handle_capture_wilt_reference,
            }
            handler = handlers.get(self.path)
            if handler is None:
                self.send_error(404)
                return
            handler(fields)

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

            current = config_module.load()
            current["wifi"]["ssid"] = ssid
            current["wifi"]["password"] = password
            current["mqtt"]["broker"] = broker
            current["mqtt"]["port"] = port
            config_module.save(current)

            discovery_started = False
            if connected and broker:
                discovery.set_broker(broker, port)
                discovery.connect()
                presence.set_broker(broker, port)
                presence.connect()
                discovery_started = True

            self._send_json({"success": True, "connected": connected, "discovery_started": discovery_started})

        def _handle_save_association(self, fields):
            raw = fields.get("base_ids", "")
            selected = [b for b in raw.split(",") if b]
            association.set_associated_base_ids(selected)
            presence.sync_associated_bases(selected)
            self._send_json({"success": True, "associated_base_ids": selected})

        def _handle_save_grid(self, fields):
            """
            Full-state replace, same convention as _handle_save_association:
            the request always carries the complete desired dims + cell
            map, not an incremental diff - a cell omitted from `cells`
            is correctly treated as unassigned rather than silently
            keeping a stale value. Routed through grid_config.set_grid()
            (one atomic load/validate/save) rather than
            set_grid_dimensions() + a per-cell set_cell_assignment()
            loop - the old per-cell-call version could partially persist
            a grid change (dimensions saved, then a later cell rejected)
            even though the request as a whole gets reported as failed.
            """
            try:
                rows = int(fields.get("rows", ""))
                cols = int(fields.get("cols", ""))
            except ValueError:
                self._send_json({"success": False, "error": "rows/cols must be numbers"}, status=400)
                return

            try:
                cells = json.loads(fields.get("cells", "{}"))
            except ValueError:
                self._send_json({"success": False, "error": "cells must be valid JSON"}, status=400)
                return

            try:
                grid_config.set_grid(rows, cols, cells)
            except ValueError as e:
                self._send_json({"success": False, "error": str(e)}, status=400)
                return

            presence.publish_global_config()
            self._send_json({"success": True, "grid": grid_config.get_grid()})

        def _handle_save_global_settings(self, fields):
            try:
                frequency = int(fields.get("frequency_per_day", ""))
                threshold = int(fields.get("flash_threshold", ""))
            except ValueError:
                self._send_json({"success": False, "error": "frequency and threshold must be numbers"}, status=400)
                return

            current = config_module.load()
            current["capture"]["frequency_per_day"] = frequency
            current["flash"]["enabled"] = fields.get("flash_enabled") == "true"
            current["flash"]["low_light_threshold"] = threshold
            config_module.save(current)
            presence.publish_global_config()
            self._send_json({"success": True})

        def _handle_save_per_base_metric(self, fields):
            base_id = fields.get("base_id", "")
            metric = fields.get("metric", "")
            if not base_id or not metric:
                self._send_json({"success": False, "error": "base_id and metric are required"}, status=400)
                return

            enabled = fields.get("enabled") == "true"
            try:
                per_base_settings.set_metric(base_id, metric, enabled, has_reference=wilt_watch.has_reference)
            except ValueError as e:
                self._send_json({"success": False, "error": str(e)}, status=400)
                return

            presence.publish_base_state(base_id)
            self._send_json({"success": True, "settings": per_base_settings.get_settings(base_id)})

        def _handle_capture_grid_reference(self, fields):
            """Grabs a fresh still to display as the grid-overlay background (item 2) - not stored/reused beyond this display purpose, unlike wilt_watch's own reference image."""
            os.makedirs(_DATA_DIR, exist_ok=True)
            ok = capture_still(_GRID_REFERENCE_PATH)
            if not ok:
                self._send_json({"success": False, "error": "capture failed - is the camera connected?"}, status=500)
                return
            self._send_json({"success": True})

        def _handle_capture_wilt_reference(self, fields):
            """
            Captures a fresh still, crops it to base_id's assigned
            grid cell(s), and stores that crop as base_id's wilt-watch
            reference (item 9) - clearing wilt_watch_config_necessary
            (see wilt_watch.capture_reference).
            """
            base_id = fields.get("base_id", "")
            if not base_id:
                self._send_json({"success": False, "error": "base_id is required"}, status=400)
                return

            os.makedirs(_DATA_DIR, exist_ok=True)
            if not capture_still(_WILT_CAPTURE_TMP_PATH):
                self._send_json({"success": False, "error": "capture failed - is the camera connected?"}, status=500)
                return

            full_image = load_image_from_file(_WILT_CAPTURE_TMP_PATH)
            bbox = grid_config.cell_pixel_bbox(base_id, full_image.width, full_image.height)
            if bbox is None:
                self._send_json({"success": False, "error": "{} has no assigned grid cell(s) yet".format(base_id)}, status=400)
                return

            cropped = full_image.crop(*bbox)
            wilt_watch.capture_reference(base_id, cropped)
            presence.publish_base_state(base_id)
            self._send_json({"success": True})

        def _associated_bases_with_names(self):
            bases = discovery.known_bases()
            result = {}
            for base_id in association.associated_base_ids():
                info = bases.get(base_id, {})
                result[base_id] = info.get("friendly_name") or base_id
            return result

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

        def _render_grid(self):
            grid = grid_config.get_grid()
            has_reference = os.path.exists(_GRID_REFERENCE_PATH)
            return _render(
                "grid.html",
                rows=str(grid["rows"]),
                cols=str(grid["cols"]),
                cells_json=_safe_json_for_script(grid["cells"]),
                bases_json=_safe_json_for_script(self._associated_bases_with_names()),
                has_reference="true" if has_reference else "false",
            )

        def _render_settings(self):
            per_base_rows = []
            for base_id, friendly_name in sorted(self._associated_bases_with_names().items()):
                settings = per_base_settings.get_settings(base_id)
                per_base_rows.append({
                    "base_id": base_id,
                    "friendly_name": friendly_name,
                    "settings": settings,
                })
            current = config_module.load()
            return _render(
                "settings.html",
                frequency_per_day=str(current["capture"]["frequency_per_day"]),
                flash_enabled="true" if current["flash"]["enabled"] else "false",
                flash_threshold=str(current["flash"]["low_light_threshold"]),
                per_base_json=_safe_json_for_script(per_base_rows),
                # Item 8: the FULL disclaimer belongs on this settings
                # page (where the six detector toggles live) - once,
                # not duplicated per-base or per-detector. html.escape()
                # even though this is a fixed constant, not user data -
                # cheap insurance, consistent with this file's existing
                # caution around anything rendered into HTML.
                detector_disclaimer=html.escape(FULL_DISCLAIMER),
            )

        def _send_grid_reference(self):
            if not os.path.exists(_GRID_REFERENCE_PATH):
                self.send_error(404)
                return
            with open(_GRID_REFERENCE_PATH, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

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


def serve_forever(
    wifi_manager, discovery, association, config_module,
    grid_config, per_base_settings, wilt_watch, presence, port=80,
):
    handler_cls = make_handler(
        wifi_manager, discovery, association, config_module,
        grid_config, per_base_settings, wilt_watch, presence,
    )
    server = ThreadingHTTPServer(("0.0.0.0", port), handler_cls)
    server.serve_forever()
