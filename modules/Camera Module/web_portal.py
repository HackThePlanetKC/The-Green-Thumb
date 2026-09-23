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
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

from camera_capture import capture_still as _real_capture_still
from image_compare import load_grayscale_from_file as _real_load_image_from_file
from image_library import crop_and_save as _real_crop_and_save
from visual_disclaimers import FULL_DISCLAIMER

_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_GRID_REFERENCE_PATH = os.path.join(_DATA_DIR, "grid_reference.jpg")
_WILT_CAPTURE_TMP_PATH = os.path.join(_DATA_DIR, "wilt_capture_tmp.jpg")
_LIBRARY_CAPTURE_TMP_PATH = os.path.join(_DATA_DIR, "library_capture_tmp.jpg")
_LIBRARY_CROP_TMP_PATH = os.path.join(_DATA_DIR, "library_crop_tmp.jpg")

# Strict validation for anything that becomes part of a filesystem path
# under image_library.py's data dir - a zone must be one of this
# module's actual associated bases (never an arbitrary string reaching
# os.path.join(), which could otherwise be used for path traversal -
# e.g. "../../etc"), and a saved-image id must look like the
# uuid4().hex[:12] format image_library.py itself generates.
_VALID_IMAGE_ID_RE = re.compile(r"^[0-9a-f]{1,32}$")
_WORKING_KINDS = ("current", "most_recent", "reference")


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
    grid_config, per_base_settings, wilt_watch, presence, image_library,
    capture_still=_real_capture_still, load_image_from_file=_real_load_image_from_file,
    crop_and_save=_real_crop_and_save,
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

    capture_still/load_image_from_file/crop_and_save are injected
    specifically so tests never need a real camera or Pillow installed
    - see camera_capture.py, image_compare.py, and image_library.py's
    own module docstrings for why each is guarded/injectable in the
    first place.
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
            elif self.path == "/library":
                self._send_html(self._render_library())
            elif self.path.startswith("/library/"):
                self._route_library_get()
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
                "/save_library_settings": self._handle_save_library_settings,
                "/api/grid/capture_reference": self._handle_capture_grid_reference,
                "/api/wilt_watch/capture_reference": self._handle_capture_wilt_reference,
            }
            handler = handlers.get(self.path)
            if handler is not None:
                handler(fields)
                return

            if self.path.startswith("/library/"):
                self._route_library_post(fields)
                return

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
            (see wilt_watch.capture_reference). Also saves an
            independent real-color copy of the same crop into the
            image library's "reference" working slot (image_library.py)
            - the two are deliberately separate files/formats (grayscale
            comparison data vs. a real viewable/downloadable photo),
            populated from the same source frame but never sharing
            storage - see decisions-and-practices.md.
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

            crop_and_save(_WILT_CAPTURE_TMP_PATH, bbox, _LIBRARY_CROP_TMP_PATH)
            image_library.record_reference(base_id, _LIBRARY_CROP_TMP_PATH)

            presence.publish_base_state(base_id)
            self._send_json({"success": True})

        # --- image library (grid/settings task's own capture routes stay
        # above; these are all new) ---

        def _route_library_get(self):
            """
            Parses "/library/<zone>[/download/<kind>[/<image_id>]]" -
            the only GET shapes under this prefix. zone/kind/image_id
            are all validated before ever touching the filesystem (see
            _valid_zone()/module-level _VALID_IMAGE_ID_RE) - image_id
            in particular must look like the uuid hex image_library.py
            itself generates, never an arbitrary path-traversal string.
            """
            parts = self.path[len("/library/"):].split("/")
            zone = parts[0]
            if not self._valid_zone(zone):
                self.send_error(404)
                return

            if len(parts) == 1:
                self._send_html(self._render_zone_images(zone))
                return

            if len(parts) == 3 and parts[1] == "download" and parts[2] in _WORKING_KINDS:
                self._send_library_image(zone, parts[2])
                return

            if len(parts) == 4 and parts[1] == "download" and parts[2] == "saved" and _VALID_IMAGE_ID_RE.match(parts[3]):
                self._send_library_image(zone, "saved", image_id=parts[3])
                return

            self.send_error(404)

        def _route_library_post(self, fields):
            """Parses "/library/<zone>/(capture|save|unpin)" - the only POST shapes under this prefix."""
            parts = self.path[len("/library/"):].split("/")
            if len(parts) != 2:
                self.send_error(404)
                return
            zone, action = parts
            if not self._valid_zone(zone):
                self.send_error(404)
                return

            if action == "capture":
                self._handle_library_capture(zone)
            elif action == "save":
                self._handle_library_save(zone, fields)
            elif action == "unpin":
                self._handle_library_unpin(zone, fields)
            else:
                self.send_error(404)

        def _valid_zone(self, zone):
            return bool(zone) and zone in association.associated_base_ids()

        def _handle_library_capture(self, zone):
            """
            Manual "capture now" - mirrors the existing grid-reference/
            wilt-watch-reference capture buttons (item 1's Current/Most
            Recent need SOME way to get a first image in, and no
            capture scheduler exists yet - see README.md "Remaining").
            Captures a fresh still, crops it to this zone's assigned
            cell(s), and rotates it into the library's Current/Most
            Recent slots (image_library.record_capture) - then, if this
            zone has opted in, publishes a downsampled thumbnail to HA
            (item 10).
            """
            os.makedirs(_DATA_DIR, exist_ok=True)
            if not capture_still(_LIBRARY_CAPTURE_TMP_PATH):
                self._send_json({"success": False, "error": "capture failed - is the camera connected?"}, status=500)
                return

            full_image = load_image_from_file(_LIBRARY_CAPTURE_TMP_PATH)
            bbox = grid_config.cell_pixel_bbox(zone, full_image.width, full_image.height)
            if bbox is None:
                self._send_json({"success": False, "error": "{} has no assigned grid cell(s) yet".format(zone)}, status=400)
                return

            crop_and_save(_LIBRARY_CAPTURE_TMP_PATH, bbox, _LIBRARY_CROP_TMP_PATH)
            image_library.record_capture(zone, _LIBRARY_CROP_TMP_PATH)

            settings = per_base_settings.get_settings(zone)
            if settings["thumbnail_passthrough_enabled"]:
                current = config_module.load()
                max_dimension = current["image_library"]["thumbnail_max_dimension"]
                thumbnail = image_library.thumbnail_bytes(zone, "current", max_dimension=max_dimension)
                if thumbnail is not None:
                    presence.publish_thumbnail(zone, thumbnail)

            self._send_json({"success": True, "zone": image_library.list_zone(zone)})

        def _handle_library_save(self, zone, fields):
            """Pins a copy of one of zone's working slots (item 2/9) - see image_library.save_image() for the cap-enforcement contract (blocks, never silently evicts, item 3)."""
            source = fields.get("source", "")
            if source not in _WORKING_KINDS:
                self._send_json({"success": False, "error": "source must be one of {}".format(_WORKING_KINDS)}, status=400)
                return

            max_saved_images = per_base_settings.get_settings(zone)["max_saved_images"]
            try:
                image_id = image_library.save_image(zone, source, max_saved_images)
            except ValueError as e:
                self._send_json({"success": False, "error": str(e)}, status=400)
                return
            self._send_json({"success": True, "image_id": image_id})

        def _handle_library_unpin(self, zone, fields):
            image_id = fields.get("image_id", "")
            try:
                image_library.unpin_image(zone, image_id)
            except ValueError as e:
                self._send_json({"success": False, "error": str(e)}, status=400)
                return
            self._send_json({"success": True})

        def _handle_save_library_settings(self, fields):
            """Per-zone image-library settings (item 5/10) - max_saved_images (int) and thumbnail_passthrough_enabled (bool), three-way parity same as _handle_save_per_base_metric."""
            base_id = fields.get("base_id", "")
            if not self._valid_zone(base_id):
                self._send_json({"success": False, "error": "base_id must be an associated base"}, status=400)
                return

            try:
                max_saved_images = int(fields.get("max_saved_images", ""))
            except ValueError:
                self._send_json({"success": False, "error": "max_saved_images must be a number"}, status=400)
                return

            try:
                per_base_settings.set_max_saved_images(base_id, max_saved_images)
                per_base_settings.set_thumbnail_passthrough_enabled(base_id, fields.get("thumbnail_enabled") == "true")
            except ValueError as e:
                self._send_json({"success": False, "error": str(e)}, status=400)
                return

            presence.publish_base_state(base_id)
            self._send_json({"success": True, "settings": per_base_settings.get_settings(base_id)})

        def _send_library_image(self, zone, kind, image_id=None):
            """
            This is BOTH the <img> preview source AND the download
            endpoint (item 11) for the same URL - deliberately no
            Content-Disposition: attachment header, which some browsers
            honor even for an <img> tag's subresource fetch and would
            silently break inline previews. The "Download" affordance
            (item 7/9) is instead the HTML `download` attribute on the
            template's <a> tags, which forces a save without needing
            any server-side header - see static/zone_images.html.
            """
            path = image_library.image_path(zone, kind, image_id=image_id)
            if path is None:
                self.send_error(404)
                return
            with open(path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

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

        def _render_library(self):
            """Item 6: an overview across every associated zone - counts, not full images, so this page stays light even with many zones/saved images."""
            zones = []
            for base_id, friendly_name in sorted(self._associated_bases_with_names().items()):
                zone_data = image_library.list_zone(base_id)
                zones.append({
                    "base_id": base_id,
                    "friendly_name": friendly_name,
                    "has_current": zone_data["current"] is not None,
                    "has_most_recent": zone_data["most_recent"] is not None,
                    "has_reference": zone_data["reference"] is not None,
                    "saved_count": len(zone_data["saved"]),
                })
            return _render("library.html", zones_json=_safe_json_for_script(zones))

        def _render_zone_images(self, zone):
            """Item 7: one zone's Current/Most Recent/Reference/pinned images, with a download link per image and the pin/unpin action (item 9), plus this zone's own library settings (item 5/10)."""
            zone_data = image_library.list_zone(zone)
            settings = per_base_settings.get_settings(zone)
            bases = discovery.known_bases()
            friendly_name = bases.get(zone, {}).get("friendly_name") or zone
            return _render(
                "zone_images.html",
                zone=html.escape(zone, quote=True),
                friendly_name=html.escape(friendly_name),
                zone_json=_safe_json_for_script(zone_data),
                max_saved_images=str(settings["max_saved_images"]),
                thumbnail_enabled="true" if settings["thumbnail_passthrough_enabled"] else "false",
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
    grid_config, per_base_settings, wilt_watch, presence, image_library, port=80,
):
    handler_cls = make_handler(
        wifi_manager, discovery, association, config_module,
        grid_config, per_base_settings, wilt_watch, presence, image_library,
    )
    server = ThreadingHTTPServer(("0.0.0.0", port), handler_cls)
    server.serve_forever()
