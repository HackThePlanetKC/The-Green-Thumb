"""
test_web_portal.py - end-to-end tests for web_portal.py's HTTP routes,
against a real ThreadingHTTPServer bound to 127.0.0.1 on an ephemeral
port - fake wifi_manager/discovery/association stand-ins, no real
hardware, network, or broker involved. Real loopback HTTP requests
exercise the actual routing/status-codes/JSON-and-HTML-rendering path,
which hand-mocking the handler's socket internals wouldn't cover as
faithfully.

Run: python3 test_web_portal.py
"""

import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as config_module  # noqa: E402
from web_portal import make_handler  # noqa: E402

_TEMP_DIR = tempfile.mkdtemp()


class _ConfigModuleAtTempPath:
    """
    Stands in for the real config module in these tests: save() writes
    to a throwaway temp file instead of this module's real config.json.
    web_portal.py calls config_module.save(cfg) with no path argument
    (correct for production, where there's exactly one real device and
    one real config file) - this wrapper is what keeps that call from
    touching the actual file during tests, the same isolation
    test_base_association.py's ConfigAtPath gives load()/save().
    """

    def __init__(self, tag):
        self._path = os.path.join(_TEMP_DIR, "{}.json".format(tag))

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
    def __init__(self, associated=None):
        self._associated = associated or []
        self.saved = None

    def associated_base_ids(self):
        return list(self._associated)

    def set_associated_base_ids(self, base_ids):
        self.saved = list(base_ids)


def start_server(wifi_manager, discovery, association, cfg, tag):
    fake_config_module = _ConfigModuleAtTempPath(tag)
    handler_cls = make_handler(wifi_manager, discovery, association, cfg, fake_config_module)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


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


def fresh_cfg(tag):
    # Loading a nonexistent path returns a fresh DEFAULT_CONFIG copy
    # (see config.py's OSError-caught fallback) - never touches disk,
    # never touches this module's real config.json.
    return config_module.load("/nonexistent/path/for/{}".format(tag))


# --- GET "/" shows setup.html when WiFi/broker isn't configured yet ---
cfg = fresh_cfg("t1")
wifi = FakeWifiManager(has_creds=False)
discovery = FakeDiscovery()
association = FakeAssociation()
server = start_server(wifi, discovery, association, cfg, "t1")
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

# --- GET "/" shows associate.html once WiFi+broker are configured ---
cfg2 = fresh_cfg("t2")
cfg2["mqtt"]["broker"] = "test.local"
wifi2 = FakeWifiManager(has_creds=True)
discovery2 = FakeDiscovery(bases={"A1B2C3": {"friendly_name": "Tomato Base"}})
association2 = FakeAssociation()
server2 = start_server(wifi2, discovery2, association2, cfg2, "t2")
port2 = server2.server_address[1]
try:
    status, body = get(port2, "/")
    check("GET / once WiFi+broker configured serves the associate page", "Associate Bases" in body)
    check("GET / associate page lists the discovered base's friendly_name", "Tomato Base" in body)
    check("GET / associate page embeds the base_id as the checkbox value", 'value="A1B2C3"' in body)
finally:
    server2.shutdown()

# --- friendly_name is HTML-escaped, not injected raw (a base's device_name/friendly_name is user-controlled data) ---
cfg3 = fresh_cfg("t3")
cfg3["mqtt"]["broker"] = "test.local"
wifi3 = FakeWifiManager(has_creds=True)
discovery3 = FakeDiscovery(bases={"D4E5F6": {"friendly_name": "<script>alert(1)</script>"}})
association3 = FakeAssociation()
server3 = start_server(wifi3, discovery3, association3, cfg3, "t3")
port3 = server3.server_address[1]
try:
    status, body = get(port3, "/associate")
    check("a friendly_name containing HTML is escaped, not injected raw", "<script>alert(1)</script>" not in body)
    check("the escaped form is present instead", "&lt;script&gt;" in body)
finally:
    server3.shutdown()

# --- POST /save_wifi: empty SSID rejected, valid save starts discovery ---
cfg4 = fresh_cfg("t4")
wifi4 = FakeWifiManager(has_creds=False)
discovery4 = FakeDiscovery()
association4 = FakeAssociation()
server4 = start_server(wifi4, discovery4, association4, cfg4, "t4")
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
    check("POST /save_wifi persisted the broker into cfg", cfg4["mqtt"]["broker"] == "test.local")
finally:
    server4.shutdown()

# --- POST /save_wifi: WiFi connects but broker is empty - no discovery started ---
cfg4b = fresh_cfg("t4b")
wifi4b = FakeWifiManager(has_creds=False)
discovery4b = FakeDiscovery()
association4b = FakeAssociation()
server4b = start_server(wifi4b, discovery4b, association4b, cfg4b, "t4b")
port4b = server4b.server_address[1]
try:
    status, body = post(port4b, "/save_wifi", "ssid=MyHomeWifi&password=x&broker=&port=1883")
    result = json.loads(body)
    check("POST /save_wifi with no broker does not start discovery", result["discovery_started"] is False)
    check("POST /save_wifi with no broker never calls discovery.connect()", discovery4b.connect_calls == 0)
finally:
    server4b.shutdown()

# --- POST /save_association ---
cfg5 = fresh_cfg("t5")
wifi5 = FakeWifiManager(has_creds=True)
discovery5 = FakeDiscovery()
association5 = FakeAssociation()
server5 = start_server(wifi5, discovery5, association5, cfg5, "t5")
port5 = server5.server_address[1]
try:
    status, body = post(port5, "/save_association", "base_ids=A1B2C3,D4E5F6")
    result = json.loads(body)
    check("POST /save_association returns 200", status == 200)
    check("POST /save_association splits and forwards the id list", association5.saved == ["A1B2C3", "D4E5F6"])
finally:
    server5.shutdown()

print()
if failures:
    print("{} check(s) failed: {}".format(len(failures), failures))
    sys.exit(1)
else:
    print("All checks passed.")
