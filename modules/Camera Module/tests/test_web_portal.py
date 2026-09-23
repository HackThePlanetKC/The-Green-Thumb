"""
test_web_portal.py - end-to-end tests for web_portal.py's HTTP routes,
against a real ThreadingHTTPServer bound to 127.0.0.1 on an ephemeral
port - fake wifi_manager/discovery/association/presence stand-ins, no
real hardware, network, or broker involved. Real loopback HTTP
requests exercise the actual routing/status-codes/JSON-and-HTML-
rendering path, which hand-mocking the handler's socket internals
wouldn't cover as faithfully.

grid_config/per_base_settings/wilt_watch use the REAL classes (against
a throwaway config file/data dir per test, via _ConfigModuleAtTempPath)
rather than fakes - they're already unit-tested standalone (see
test_grid_config.py/test_per_base_settings.py/test_wilt_watch.py), so
using the real thing here additionally verifies the actual wiring
between web_portal.py and those managers, not just its own logic in
isolation.

Run: python3 test_web_portal.py
"""

import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402

import config as config_module  # noqa: E402
from grid_config import GridConfigManager  # noqa: E402
from image_compare import GrayscaleImage  # noqa: E402
from image_library import ImageLibrary  # noqa: E402
from per_base_settings import PerBaseSettingsManager  # noqa: E402
from web_portal import make_handler  # noqa: E402
from wilt_watch import WiltWatchManager  # noqa: E402

_TEMP_DIR = tempfile.mkdtemp()


def _default_crop_and_save(source_path, bbox, dest_path):
    """
    Fake crop_and_save() for tests: writes a real, small, valid JPEG
    (not a byte-for-byte crop of source_path/bbox - the fake
    capture_still() these tests use doesn't write a real photo to crop
    in the first place) so downstream real code (image_library.py's
    thumbnail_bytes(), which opens the file with Pillow) still works
    against genuine image bytes rather than needing its own special
    case.
    """
    Image.new("RGB", (20, 20), (10, 200, 10)).save(dest_path, "JPEG")


class _ConfigModuleAtTempPath:
    """
    Stands in for the real config module in these tests: load()/save()
    read/write a throwaway temp file instead of this module's real
    config.json - the same isolation test_base_association.py's
    ConfigAtPath gives, now used by every manager here (grid_config,
    per_base_settings, wilt_watch, and web_portal.py itself), since
    web_portal.py no longer holds any config state of its own - see
    web_portal.py's make_handler docstring for why.
    """

    def __init__(self, tag):
        self._path = os.path.join(_TEMP_DIR, "{}.json".format(tag))

    def load(self):
        return config_module.load(self._path)

    def save(self, cfg):
        config_module.save(cfg, self._path)


failures = []


def check(label, cond):
    status = "PASS" if cond else "FAIL"
    print("[{}] {}".format(status, label))
    if not cond:
        failures.append(label)


class FakeWifiManager:
    def __init__(self, has_creds=False, scan_result=None, connect_result=True):
        self._has_creds = has_creds
        self._scan_result = scan_result or []
        self._connect_result = connect_result
        self.set_credentials_calls = []
        self.connect_sta_calls = 0

    def has_credentials(self):
        return self._has_creds

    def set_credentials(self, ssid, password):
        self.set_credentials_calls.append((ssid, password))
        self._has_creds = bool(ssid)

    def connect_sta(self):
        self.connect_sta_calls += 1
        return self._connect_result

    def scan_networks(self):
        return self._scan_result


class FakeDiscovery:
    def __init__(self, bases=None):
        self._bases = bases or {}
        self.set_broker_calls = []
        self.connect_calls = 0

    def known_bases(self):
        return dict(self._bases)

    def set_broker(self, broker, port):
        self.set_broker_calls.append((broker, port))

    def connect(self):
        self.connect_calls += 1


class FakeAssociation:
    """
    NOTE: purely in-memory, unlike the real BaseAssociationManager -
    fine for the wifi/associate-focused tests below (which never also
    touch grid_config, so the in-memory/on-disk split doesn't matter
    there), but the grid/settings tests further down use the REAL
    BaseAssociationManager instead, since grid_config.set_cell_
    assignment() validates against config.json's own
    associated_base_ids, which this fake never writes to.
    """

    def __init__(self, associated=None):
        self._associated = associated or []
        self.saved = None

    def associated_base_ids(self):
        return list(self._associated)

    def set_associated_base_ids(self, base_ids):
        self.saved = list(base_ids)
        self._associated = list(base_ids)


class FakePresence:
    def __init__(self):
        self.set_broker_calls = []
        self.connect_calls = 0
        self.sync_calls = []
        self.publish_global_config_calls = 0
        self.publish_base_state_calls = []
        self.publish_thumbnail_calls = []

    def set_broker(self, broker, port):
        self.set_broker_calls.append((broker, port))

    def connect(self):
        self.connect_calls += 1

    def sync_associated_bases(self, base_ids):
        self.sync_calls.append(list(base_ids))

    def publish_global_config(self):
        self.publish_global_config_calls += 1

    def publish_base_state(self, base_id):
        self.publish_base_state_calls.append(base_id)

    def publish_thumbnail(self, base_id, jpeg_bytes):
        self.publish_thumbnail_calls.append((base_id, jpeg_bytes))


def start_server(
    wifi_manager, discovery, association, tag,
    grid_config=None, per_base_settings=None, wilt_watch=None, presence=None,
    image_library=None, capture_still=None, load_image_from_file=None, crop_and_save=None,
):
    fake_config_module = _ConfigModuleAtTempPath(tag)
    data_dir = os.path.join(_TEMP_DIR, "{}_data".format(tag))
    per_base_settings = per_base_settings or PerBaseSettingsManager(fake_config_module)
    grid_config = grid_config or GridConfigManager(fake_config_module)
    wilt_watch = wilt_watch or WiltWatchManager(per_base_settings, data_dir=data_dir)
    presence = presence or FakePresence()
    image_library = image_library or ImageLibrary(data_dir=os.path.join(data_dir, "library"))
    capture_still = capture_still or (lambda path: True)
    load_image_from_file = load_image_from_file or (lambda path: GrayscaleImage(4, 4, [200] * 16))
    crop_and_save = crop_and_save or _default_crop_and_save

    handler_cls = make_handler(
        wifi_manager, discovery, association, fake_config_module,
        grid_config, per_base_settings, wilt_watch, presence, image_library,
        capture_still=capture_still, load_image_from_file=load_image_from_file, crop_and_save=crop_and_save,
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, fake_config_module, grid_config, per_base_settings, wilt_watch, presence, image_library


def get(port, path):
    try:
        with urllib.request.urlopen("http://127.0.0.1:{}{}".format(port, path)) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def post(port, path, body):
    req = urllib.request.Request(
        "http://127.0.0.1:{}{}".format(port, path), data=body.encode(), method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


# --- GET "/" shows setup.html when WiFi/broker isn't configured yet ---
wifi = FakeWifiManager(has_creds=False)
discovery = FakeDiscovery()
association = FakeAssociation()
server, fake_cfg1, *_ = start_server(wifi, discovery, association, "t1")
port = server.server_address[1]
try:
    status, body = get(port, "/")
    check("GET / with no WiFi/broker configured returns 200", status == 200)
    check("GET / with no WiFi/broker configured serves the setup page", "Camera Module Setup" in body)

    status, body = get(port, "/api/scan")
    check("GET /api/scan returns 200", status == 200)
    check("GET /api/scan returns the fake scan result as JSON", json.loads(body)["networks"] == [])

    status, body = get(port, "/nonexistent-route")
    check("GET on an unknown route returns 404", status == 404)
finally:
    server.shutdown()

# --- GET "/" shows associate.html once WiFi+broker are configured but no base is associated yet ---
fake_cfg2 = _ConfigModuleAtTempPath("t2")
cfg2 = fake_cfg2.load()
cfg2["mqtt"]["broker"] = "test.local"
fake_cfg2.save(cfg2)
wifi2 = FakeWifiManager(has_creds=True)
discovery2 = FakeDiscovery(bases={"A1B2C3": {"friendly_name": "Tomato Base"}})
association2 = FakeAssociation()
server2, *_ = start_server(wifi2, discovery2, association2, "t2")
port2 = server2.server_address[1]
try:
    status, body = get(port2, "/")
    check("GET / once WiFi+broker configured but no association yet serves the associate page", "Associate Bases" in body)
    check("GET / associate page lists the discovered base's friendly_name", "Tomato Base" in body)
    check("GET / associate page embeds the base_id as the checkbox value", 'value="A1B2C3"' in body)
    check("associate page links onward to grid layout", 'href="/grid"' in body)
finally:
    server2.shutdown()

# --- GET "/" shows the dashboard once at least one base is associated ---
fake_cfg2b = _ConfigModuleAtTempPath("t2b")
cfg2b = fake_cfg2b.load()
cfg2b["mqtt"]["broker"] = "test.local"
fake_cfg2b.save(cfg2b)
wifi2b = FakeWifiManager(has_creds=True)
discovery2b = FakeDiscovery()
association2b = FakeAssociation(["A1B2C3"])
server2b, *_ = start_server(wifi2b, discovery2b, association2b, "t2b")
port2b = server2b.server_address[1]
try:
    status, body = get(port2b, "/")
    check("GET / once a base is associated serves the dashboard, not associate.html again", "Associate Bases" not in body and "Camera Module</h1>" in body)
    check("dashboard links to /grid and /settings", 'href="/grid"' in body and 'href="/settings"' in body)
finally:
    server2b.shutdown()

# --- friendly_name is HTML-escaped, not injected raw (a base's device_name/friendly_name is user-controlled data) ---
fake_cfg3 = _ConfigModuleAtTempPath("t3")
cfg3 = fake_cfg3.load()
cfg3["mqtt"]["broker"] = "test.local"
fake_cfg3.save(cfg3)
wifi3 = FakeWifiManager(has_creds=True)
discovery3 = FakeDiscovery(bases={"D4E5F6": {"friendly_name": "<script>alert(1)</script>"}})
association3 = FakeAssociation()
server3, *_ = start_server(wifi3, discovery3, association3, "t3")
port3 = server3.server_address[1]
try:
    status, body = get(port3, "/associate")
    check("a friendly_name containing HTML is escaped, not injected raw", "<script>alert(1)</script>" not in body)
    check("the escaped form is present instead", "&lt;script&gt;" in body)
finally:
    server3.shutdown()

# --- POST /save_wifi: empty SSID rejected, valid save starts discovery + presence ---
wifi4 = FakeWifiManager(has_creds=False)
discovery4 = FakeDiscovery()
association4 = FakeAssociation()
server4, fake_cfg4, _, _, _, presence4, _ = start_server(wifi4, discovery4, association4, "t4")
port4 = server4.server_address[1]
try:
    status, body = post(port4, "/save_wifi", "ssid=&password=x&broker=test.local&port=1883")
    check("POST /save_wifi with an empty SSID returns 400", status == 400)
    check("POST /save_wifi with an empty SSID reports failure", json.loads(body)["success"] is False)

    status, body = post(port4, "/save_wifi", "ssid=MyHomeWifi&password=hunter2&broker=test.local&port=1883")
    result = json.loads(body)
    check("POST /save_wifi with valid fields returns 200", status == 200)
    check("POST /save_wifi reports success", result["success"] is True)
    check("POST /save_wifi reports connected", result["connected"] is True)
    check("POST /save_wifi starts discovery once WiFi+broker both succeed", result["discovery_started"] is True)
    check("POST /save_wifi called wifi_manager.set_credentials()", wifi4.set_credentials_calls == [("MyHomeWifi", "hunter2")])
    check("POST /save_wifi called discovery.set_broker()", discovery4.set_broker_calls == [("test.local", 1883)])
    check("POST /save_wifi called discovery.connect()", discovery4.connect_calls == 1)
    check("POST /save_wifi also connects the module's own MQTT presence", presence4.set_broker_calls == [("test.local", 1883)] and presence4.connect_calls == 1)
    check("POST /save_wifi persisted the broker to disk", fake_cfg4.load()["mqtt"]["broker"] == "test.local")
finally:
    server4.shutdown()

# --- POST /save_wifi: WiFi connects but broker is empty - no discovery/presence started ---
wifi4b = FakeWifiManager(has_creds=False)
discovery4b = FakeDiscovery()
association4b = FakeAssociation()
server4b, _, _, _, _, presence4b, _ = start_server(wifi4b, discovery4b, association4b, "t4b")
port4b = server4b.server_address[1]
try:
    status, body = post(port4b, "/save_wifi", "ssid=MyHomeWifi&password=x&broker=&port=1883")
    result = json.loads(body)
    check("POST /save_wifi with no broker does not start discovery", result["discovery_started"] is False)
    check("POST /save_wifi with no broker never calls discovery.connect()", discovery4b.connect_calls == 0)
    check("POST /save_wifi with no broker never connects presence either", presence4b.connect_calls == 0)
finally:
    server4b.shutdown()

# --- POST /save_association ---
wifi5 = FakeWifiManager(has_creds=True)
discovery5 = FakeDiscovery()
association5 = FakeAssociation()
server5, _, _, _, _, presence5, _ = start_server(wifi5, discovery5, association5, "t5")
port5 = server5.server_address[1]
try:
    status, body = post(port5, "/save_association", "base_ids=A1B2C3,D4E5F6")
    result = json.loads(body)
    check("POST /save_association returns 200", status == 200)
    check("POST /save_association splits and forwards the id list", association5.saved == ["A1B2C3", "D4E5F6"])
    check("POST /save_association tells presence to (re)sync per-base topics", presence5.sync_calls == [["A1B2C3", "D4E5F6"]])
finally:
    server5.shutdown()

# --- GET /grid + POST /save_grid: full grid/cell-assignment flow ---
fake_cfg6 = _ConfigModuleAtTempPath("t6")
cfg6 = fake_cfg6.load()
cfg6["associated_base_ids"] = ["A1B2C3", "D4E5F6"]
fake_cfg6.save(cfg6)
wifi6 = FakeWifiManager(has_creds=True)
discovery6 = FakeDiscovery(bases={"A1B2C3": {"friendly_name": "Tomato Base"}, "D4E5F6": {"friendly_name": "Basil Base"}})
association6 = FakeAssociation(["A1B2C3", "D4E5F6"])
server6, fake_cfg6b, grid6, _, _, presence6, _ = start_server(wifi6, discovery6, association6, "t6")
port6 = server6.server_address[1]
try:
    status, body = get(port6, "/grid")
    check("GET /grid returns 200", status == 200)
    check("GET /grid embeds the associated bases for the cell dropdowns", "Tomato Base" in body and "Basil Base" in body)

    status, body = get(port6, "/api/grid/reference.jpg")
    check("GET /api/grid/reference.jpg 404s before any reference has been captured", status == 404)

    status, body = post(port6, "/api/grid/capture_reference", "")
    check("POST /api/grid/capture_reference succeeds with the fake capture_still", json.loads(body)["success"] is True)

    cells = json.dumps({"0,0": "A1B2C3", "0,1": "A1B2C3", "1,0": "D4E5F6"})
    status, body = post(port6, "/save_grid", "rows=2&cols=2&cells=" + urllib.parse.quote(cells))
    result = json.loads(body)
    check("POST /save_grid returns 200", status == 200)
    check("POST /save_grid persists the dimensions", result["grid"]["rows"] == 2 and result["grid"]["cols"] == 2)
    check("POST /save_grid persists the full cell map", result["grid"]["cells"] == {"0,0": "A1B2C3", "0,1": "A1B2C3", "1,0": "D4E5F6"})
    check("POST /save_grid republishes global MQTT config", presence6.publish_global_config_calls >= 1)

    # unassigned cell (1,1) is correctly absent, and an omitted cell clears any prior value
    cells2 = json.dumps({"0,0": "D4E5F6"})
    post(port6, "/save_grid", "rows=2&cols=2&cells=" + urllib.parse.quote(cells2))
    check("a cell omitted from a later save is cleared, not left stale", grid6.get_grid()["cells"] == {"0,0": "D4E5F6"})

    status, body = post(port6, "/save_grid", "rows=2&cols=2&cells=" + urllib.parse.quote(json.dumps({"0,0": "NOT_A_REAL_BASE"})))
    check("assigning a cell to a non-associated base returns 400", status == 400)
finally:
    server6.shutdown()

# --- GET /settings + POST /save_global_settings + POST /save_per_base_metric ---
fake_cfg7 = _ConfigModuleAtTempPath("t7")
cfg7 = fake_cfg7.load()
cfg7["associated_base_ids"] = ["A1B2C3"]
fake_cfg7.save(cfg7)
wifi7 = FakeWifiManager(has_creds=True)
discovery7 = FakeDiscovery(bases={"A1B2C3": {"friendly_name": "Tomato Base"}})
association7 = FakeAssociation(["A1B2C3"])
server7, fake_cfg7b, _, per_base7, wilt7, presence7, image_library7 = start_server(wifi7, discovery7, association7, "t7")
port7 = server7.server_address[1]
try:
    status, body = get(port7, "/settings")
    check("GET /settings returns 200", status == 200)
    check("GET /settings embeds the associated base for per-base toggles", "Tomato Base" in body)
    check("GET /settings shows the full detector disclaimer (item 8)", "NOT diagnostic-grade" in body and "qualified professional" in body)

    status, body = post(port7, "/save_global_settings", "frequency_per_day=4&flash_enabled=true&flash_threshold=75")
    check("POST /save_global_settings returns 200", status == 200)
    saved = fake_cfg7b.load()
    check("POST /save_global_settings persists capture frequency", saved["capture"]["frequency_per_day"] == 4)
    check("POST /save_global_settings persists flash.enabled", saved["flash"]["enabled"] is True)
    check("POST /save_global_settings persists the flash threshold", saved["flash"]["low_light_threshold"] == 75)
    check("POST /save_global_settings republishes global MQTT config", presence7.publish_global_config_calls == 1)

    status, body = post(port7, "/save_per_base_metric", "base_id=A1B2C3&metric=chlorosis&enabled=true")
    result = json.loads(body)
    check("POST /save_per_base_metric returns 200", status == 200)
    check("POST /save_per_base_metric persists the toggle", per_base7.get_settings("A1B2C3")["chlorosis"] is True)
    check("POST /save_per_base_metric republishes that base's MQTT state", presence7.publish_base_state_calls == ["A1B2C3"])
    check("POST /save_per_base_metric response reflects the updated settings", result["settings"]["chlorosis"] is True)

    # enabling wilt_watch surfaces config_necessary in the response, for the JS prompt
    status, body = post(port7, "/save_per_base_metric", "base_id=A1B2C3&metric=wilt_watch&enabled=true")
    result = json.loads(body)
    check("enabling wilt_watch via the portal reports config_necessary=True", result["settings"]["wilt_watch_config_necessary"] is True)

    status, body = post(port7, "/save_per_base_metric", "base_id=A1B2C3&metric=general_health&enabled=false")
    check("attempting to disable general_health via the portal returns 400", status == 400)

    # capturing the wilt-watch reference needs an assigned cell first
    status, body = post(port7, "/api/wilt_watch/capture_reference", "base_id=A1B2C3")
    check("capturing a wilt-watch reference with no assigned grid cell returns 400", status == 400)

    grid7 = GridConfigManager(fake_cfg7b)
    grid7.set_grid_dimensions(1, 1)
    grid7.set_cell_assignment(0, 0, "A1B2C3")
    status, body = post(port7, "/api/wilt_watch/capture_reference", "base_id=A1B2C3")
    check("capturing a wilt-watch reference once a cell is assigned succeeds", json.loads(body)["success"] is True)
    check("a captured wilt-watch reference clears config_necessary", per_base7.get_settings("A1B2C3")["wilt_watch_config_necessary"] is False)
    check("a captured wilt-watch reference is stored and usable for comparison", wilt7.has_reference("A1B2C3") is True)
    check("capturing a wilt-watch reference ALSO populates the image library's independent reference slot", image_library7.list_zone("A1B2C3")["reference"] is not None)

    # the three new heuristic visual detector toggles (leaf_scorch, powdery_mildew, pest_indicators)
    # go through the exact same route as the pre-existing ones - no special-casing needed
    for new_metric in ("leaf_scorch", "powdery_mildew", "pest_indicators"):
        status, body = post(port7, "/save_per_base_metric", "base_id=A1B2C3&metric={}&enabled=true".format(new_metric))
        result = json.loads(body)
        check("POST /save_per_base_metric accepts the new '{}' toggle".format(new_metric), status == 200 and result["success"] is True)
        check("'{}' is persisted".format(new_metric), per_base7.get_settings("A1B2C3")[new_metric] is True)
finally:
    server7.shutdown()

# --- image library: GET /library, GET/POST /library/<zone>/..., POST /save_library_settings ---
fake_cfg8 = _ConfigModuleAtTempPath("t8")
cfg8 = fake_cfg8.load()
cfg8["associated_base_ids"] = ["A1B2C3"]
fake_cfg8.save(cfg8)
wifi8 = FakeWifiManager(has_creds=True)
discovery8 = FakeDiscovery(bases={"A1B2C3": {"friendly_name": "Tomato Base"}})
association8 = FakeAssociation(["A1B2C3"])
server8, fake_cfg8b, _, per_base8, _, presence8, image_library8 = start_server(wifi8, discovery8, association8, "t8")
port8 = server8.server_address[1]
try:
    status, body = get(port8, "/library")
    check("GET /library returns 200", status == 200)
    check("GET /library embeds the associated zone", "Tomato Base" in body)

    status, body = get(port8, "/library/A1B2C3")
    check("GET /library/<zone> for an associated zone returns 200", status == 200)
    check("GET /library/<zone> shows the zone's friendly name", "Tomato Base" in body)

    status, body = get(port8, "/library/UNKNOWN_ZONE")
    check("GET /library/<zone> for a NON-associated zone returns 404 (not a filesystem lookup)", status == 404)

    status, body = get(port8, "/library/A1B2C3/download/current")
    check("GET download for a slot that doesn't exist yet returns 404", status == 404)

    # --- path-traversal / validation hardening: zone and image_id are validated BEFORE touching the filesystem ---
    status, body = get(port8, "/library/..%2f..%2f..%2fetc/download/current")
    check("a path-traversal-shaped zone is rejected (not treated as a filesystem path)", status == 404)

    status, body = get(port8, "/library/A1B2C3/download/saved/..%2f..%2fsomething")
    check("a path-traversal-shaped saved image_id is rejected (doesn't match the hex-id pattern)", status == 404)

    status, body = get(port8, "/library/A1B2C3/download/not_a_real_kind")
    check("an unknown download kind returns 404", status == 404)

    # --- capture (no grid cell assigned yet): fails clearly ---
    status, body = post(port8, "/library/A1B2C3/capture", "")
    check("POST capture with no assigned grid cell returns 400", status == 400)

    grid8 = GridConfigManager(fake_cfg8b)
    grid8.set_grid_dimensions(1, 1)
    grid8.set_cell_assignment(0, 0, "A1B2C3")

    # --- capture now: rotates into current, serves via download, and the SAME URL renders inline (no Content-Disposition) ---
    status, body = post(port8, "/library/A1B2C3/capture", "")
    result = json.loads(body)
    check("POST capture succeeds once a cell is assigned", status == 200 and result["success"] is True)
    check("the capture response includes the zone's updated state", result["zone"]["current"] is not None)

    req = urllib.request.Request("http://127.0.0.1:{}/library/A1B2C3/download/current".format(port8))
    with urllib.request.urlopen(req) as resp:
        check("GET download for current now returns 200", resp.status == 200)
        check("download response Content-Type is image/jpeg", resp.headers.get("Content-Type") == "image/jpeg")
        check("download response has NO Content-Disposition header (so <img> previews still render inline)", resp.headers.get("Content-Disposition") is None)

    check("a fresh capture does not yet populate most_recent (nothing to rotate on the first capture)", image_library8.list_zone("A1B2C3")["most_recent"] is None)

    # a second capture rotates current -> most_recent
    post(port8, "/library/A1B2C3/capture", "")
    check("a second capture populates most_recent (rotation)", image_library8.list_zone("A1B2C3")["most_recent"] is not None)

    # --- pin/save ---
    status, body = post(port8, "/library/A1B2C3/save", "source=current")
    result = json.loads(body)
    check("POST save (pin) succeeds for a valid source", status == 200 and result["success"] is True)
    image_id = result["image_id"]
    check("the pinned image appears in the zone's saved set", image_id in image_library8.list_zone("A1B2C3")["saved"])

    req = urllib.request.Request("http://127.0.0.1:{}/library/A1B2C3/download/saved/{}".format(port8, image_id))
    with urllib.request.urlopen(req) as resp:
        check("GET download for a saved/pinned image returns 200", resp.status == 200)
        check("download response for a saved image is also image/jpeg", resp.headers.get("Content-Type") == "image/jpeg")

    status, body = post(port8, "/library/A1B2C3/save", "source=not_a_real_source")
    check("POST save with an invalid source returns 400", status == 400)

    # --- per-zone library settings: three-way-parity setting (item 5/10) ---
    status, body = post(port8, "/save_library_settings", "base_id=A1B2C3&max_saved_images=1&thumbnail_enabled=true")
    result = json.loads(body)
    check("POST save_library_settings succeeds", status == 200 and result["success"] is True)
    check("max_saved_images is persisted", per_base8.get_settings("A1B2C3")["max_saved_images"] == 1)
    check("thumbnail_passthrough_enabled is persisted", per_base8.get_settings("A1B2C3")["thumbnail_passthrough_enabled"] is True)
    check("save_library_settings republishes the base's MQTT state", "A1B2C3" in presence8.publish_base_state_calls)

    # cap is now 1, and one image is already pinned - a second pin is blocked, not silently evicting the first
    status, body = post(port8, "/library/A1B2C3/save", "source=most_recent")
    check("pinning beyond the (now-lowered) cap returns 400", status == 400)
    check("the original pinned image is still present (not silently evicted)", image_id in image_library8.list_zone("A1B2C3")["saved"])

    # --- unpin ---
    status, body = post(port8, "/library/A1B2C3/unpin", "image_id={}".format(image_id))
    check("POST unpin succeeds", status == 200 and json.loads(body)["success"] is True)
    check("the image is gone from the saved set", image_id not in image_library8.list_zone("A1B2C3")["saved"])

    status, body = post(port8, "/library/A1B2C3/unpin", "image_id=not-a-real-id")
    check("POST unpin for an unknown image_id returns 400", status == 400)

    # --- thumbnail passthrough: opted in above, so the NEXT capture should publish one ---
    presence8.publish_thumbnail_calls.clear()
    post(port8, "/library/A1B2C3/capture", "")
    check("a capture publishes an MQTT thumbnail once this zone has opted in", len(presence8.publish_thumbnail_calls) == 1)
    check("the published thumbnail is for the right zone", presence8.publish_thumbnail_calls[0][0] == "A1B2C3")
    check("the published thumbnail is non-empty bytes", len(presence8.publish_thumbnail_calls[0][1]) > 0)

    # opting back out stops future captures from publishing a thumbnail
    post(port8, "/save_library_settings", "base_id=A1B2C3&max_saved_images=10&thumbnail_enabled=false")
    presence8.publish_thumbnail_calls.clear()
    post(port8, "/library/A1B2C3/capture", "")
    check("a capture does NOT publish a thumbnail once opted back out", presence8.publish_thumbnail_calls == [])
finally:
    server8.shutdown()

print()
if failures:
    print("{} check(s) failed: {}".format(len(failures), failures))
    sys.exit(1)
else:
    print("All checks passed.")
