"""
test_mqtt_presence.py - stub-based tests for
mqtt_presence.CameraMqttPresence: no real broker required - a fake
paho-mqtt-shaped client captures every publish/subscribe call, and
handle_message() is exercised directly for command handling, same
split as test_mqtt_discovery.py.

Specifically verifies (per this task's item 16): the module-global
topic tree (greenthumb/camera/<camera_id>/global/...) is isolated from
each base's own per-base topic tree (greenthumb/<base_id>/module/
<camera_id>/...) - no per-base data leaks into the global topics and
vice versa.

Run: python3 test_mqtt_presence.py
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config as config_module  # noqa: E402
from mqtt_presence import CameraMqttPresence  # noqa: E402
from per_base_settings import PerBaseSettingsManager  # noqa: E402

failures = []


def check(label, cond):
    status = "PASS" if cond else "FAIL"
    print("[{}] {}".format(status, label))
    if not cond:
        failures.append(label)


class ConfigAtPath:
    def __init__(self, path):
        self._path = path

    def load(self):
        return config_module.load(self._path)

    def save(self, cfg):
        config_module.save(cfg, self._path)


class FakeAssociation:
    def __init__(self, base_ids=None):
        self._base_ids = base_ids or []

    def associated_base_ids(self):
        return list(self._base_ids)


class FakeMqttClient:
    def __init__(self):
        self.on_connect = None
        self.on_message = None
        self.connected_to = None
        self.will = None
        self.subscriptions = []
        self.unsubscriptions = []
        self.published = []  # (topic, payload, retain, qos)
        self.loop_started = False
        self.loop_stopped = False
        self.disconnected = False

    def will_set(self, topic, payload, retain=False, qos=0):
        self.will = (topic, payload, retain, qos)

    def connect(self, broker, port, keepalive=60):
        self.connected_to = (broker, port)

    def loop_start(self):
        self.loop_started = True
        if self.on_connect:
            self.on_connect(self, None, {}, 0, None)

    def loop_stop(self):
        self.loop_stopped = True

    def disconnect(self):
        self.disconnected = True

    def subscribe(self, topic, qos=0):
        self.subscriptions.append((topic, qos))

    def unsubscribe(self, topic):
        self.unsubscriptions.append(topic)

    def publish(self, topic, payload, retain=False, qos=0):
        self.published.append((topic, payload, retain, qos))

    def published_to(self, topic):
        return [p for p in self.published if p[0] == topic]


with tempfile.TemporaryDirectory() as d:
    config_path = os.path.join(d, "config.json")
    bound_config = ConfigAtPath(config_path)
    per_base_settings = PerBaseSettingsManager(bound_config)
    association = FakeAssociation(["A1B2C3"])

    fake_client = FakeMqttClient()
    presence = CameraMqttPresence(
        bound_config, association, per_base_settings, "CAM001",
        broker="test.local", port=1883, client_factory=lambda: fake_client,
    )
    presence.connect()

    check("connect() connects to the configured broker/port", fake_client.connected_to == ("test.local", 1883))
    check("connect() sets a retained offline LWT on the global status topic", fake_client.will == ("greenthumb/camera/CAM001/global/status", b"offline", True, 1))

    global_topics = {t for t, _, _, _ in fake_client.published if t.startswith("greenthumb/camera/CAM001/global/")}
    base_topics = {t for t, _, _, _ in fake_client.published if t.startswith("greenthumb/A1B2C3/module/CAM001/")}

    check("global status is published online, retained", fake_client.published_to("greenthumb/camera/CAM001/global/status") == [("greenthumb/camera/CAM001/global/status", b"online", True, 1)])
    device_info_payload = json.loads(fake_client.published_to("greenthumb/camera/CAM001/global/device_info")[0][1])
    check("global device_info carries camera_id", device_info_payload["camera_id"] == "CAM001")
    check("global device_info carries a version string", isinstance(device_info_payload.get("version"), str))
    global_config_payload = json.loads(fake_client.published_to("greenthumb/camera/CAM001/global/config")[0][1])
    check("global config publishes only capture/grid/flash keys", set(global_config_payload.keys()) == {"capture", "grid", "flash"})
    check("subscribed to the global command topic", ("greenthumb/camera/CAM001/global/command", 1) in fake_client.subscriptions)

    check("subscribed to the associated base's command topic", ("greenthumb/A1B2C3/module/CAM001/command", 1) in fake_client.subscriptions)
    check("associated base's status published online, retained", fake_client.published_to("greenthumb/A1B2C3/module/CAM001/status") == [("greenthumb/A1B2C3/module/CAM001/status", b"online", True, 1)])
    base_state_payload = json.loads(fake_client.published_to("greenthumb/A1B2C3/module/CAM001/state")[0][1])
    check("associated base's state carries general_health always-on", base_state_payload["general_health"] is True)

    # --- item 14/16: global and per-base topic trees never overlap ---
    check("no per-base data is published under the global/ prefix", not any("A1B2C3" in t for t in global_topics))
    check("no global-only keys (capture/grid/flash) leak into the base's own state payload", not any(k in base_state_payload for k in ("capture", "grid", "flash")))
    check("global and per-base topic sets are disjoint", global_topics.isdisjoint(base_topics))

    # --- global set_config command: whitelisted path succeeds ---
    fake_client.published.clear()
    presence.handle_message("greenthumb/camera/CAM001/global/command", json.dumps({"action": "set_config", "path": "flash.enabled", "value": False}))
    check("a whitelisted global set_config path updates and persists config", bound_config.load()["flash"]["enabled"] is False)
    check("a successful global set_config republishes global/config", len(fake_client.published_to("greenthumb/camera/CAM001/global/config")) == 1)

    # --- global set_config command: disallowed top-level key (credentials) is rejected silently ---
    fake_client.published.clear()
    presence.handle_message("greenthumb/camera/CAM001/global/command", json.dumps({"action": "set_config", "path": "wifi.ssid", "value": "hacked"}))
    check("a non-whitelisted path (wifi.ssid) is rejected - credentials untouched", bound_config.load()["wifi"]["ssid"] == "")
    check("a rejected global command does not republish global/config", fake_client.published_to("greenthumb/camera/CAM001/global/config") == [])

    # --- global set_config command: unknown path under an allowed top-level key is rejected ---
    fake_client.published.clear()
    presence.handle_message("greenthumb/camera/CAM001/global/command", json.dumps({"action": "set_config", "path": "capture.does_not_exist", "value": 1}))
    check("an unknown path under an allowed key is rejected (KeyError caught)", fake_client.published_to("greenthumb/camera/CAM001/global/config") == [])

    # --- per-base set_metric command ---
    fake_client.published.clear()
    presence.handle_message("greenthumb/A1B2C3/module/CAM001/command", json.dumps({"action": "set_metric", "metric": "chlorosis", "enabled": True}))
    check("a base's set_metric command updates that base's settings", per_base_settings.get_settings("A1B2C3")["chlorosis"] is True)
    republished_state = json.loads(fake_client.published_to("greenthumb/A1B2C3/module/CAM001/state")[0][1])
    check("a successful set_metric republishes that base's state topic", republished_state["chlorosis"] is True)

    # --- per-base set_metric command: invalid metric rejected silently, no republish ---
    fake_client.published.clear()
    presence.handle_message("greenthumb/A1B2C3/module/CAM001/command", json.dumps({"action": "set_metric", "metric": "general_health", "enabled": False}))
    check("attempting to toggle general_health via MQTT is rejected", per_base_settings.get_settings("A1B2C3")["general_health"] is True)
    check("a rejected set_metric does not republish state", fake_client.published_to("greenthumb/A1B2C3/module/CAM001/state") == [])

    # --- a command topic matching the shape but for a base_id not currently associated is still processed, not crashed on - no association-membership check at the MQTT layer itself (association is enforced by sync_associated_bases()'s subscribe/unsubscribe, not by handle_message) ---
    fake_client.published.clear()
    presence.handle_message("greenthumb/UNKNOWN99/module/CAM001/command", json.dumps({"action": "set_metric", "metric": "chlorosis", "enabled": True}))
    check("a set_metric command for an unassociated base_id is still applied without raising", fake_client.published_to("greenthumb/UNKNOWN99/module/CAM001/state") != [])

    # --- sync_associated_bases: removing a base unsubscribes and marks it offline; adding a new one subscribes ---
    fake_client.subscriptions.clear()
    fake_client.unsubscriptions.clear()
    fake_client.published.clear()
    presence.sync_associated_bases(["D4E5F6"])
    check("removing a previously-associated base unsubscribes its command topic", "greenthumb/A1B2C3/module/CAM001/command" in fake_client.unsubscriptions)
    check("removing a previously-associated base publishes it offline, retained", fake_client.published_to("greenthumb/A1B2C3/module/CAM001/status") == [("greenthumb/A1B2C3/module/CAM001/status", b"offline", True, 1)])
    check("a newly-associated base is subscribed", ("greenthumb/D4E5F6/module/CAM001/command", 1) in fake_client.subscriptions)
    check("a newly-associated base is published online", fake_client.published_to("greenthumb/D4E5F6/module/CAM001/status") == [("greenthumb/D4E5F6/module/CAM001/status", b"online", True, 1)])

    presence.disconnect()
    check("disconnect() stops the loop and disconnects", fake_client.loop_stopped is True and fake_client.disconnected is True)

# --- connect() without a broker configured raises clearly ---
with tempfile.TemporaryDirectory() as d2:
    bound_config2 = ConfigAtPath(os.path.join(d2, "config.json"))
    presence_no_broker = CameraMqttPresence(bound_config2, FakeAssociation([]), PerBaseSettingsManager(bound_config2), "CAM002", broker=None)
    try:
        presence_no_broker.connect()
        raised_no_broker = False
    except RuntimeError:
        raised_no_broker = True
    check("connect() with no broker configured raises RuntimeError", raised_no_broker is True)

print()
if failures:
    print("{} check(s) failed: {}".format(len(failures), failures))
    sys.exit(1)
else:
    print("All checks passed.")
