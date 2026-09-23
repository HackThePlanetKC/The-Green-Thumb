"""
test_mqtt_discovery.py - stub-based tests for mqtt_discovery.BaseDiscovery.

No real MQTT broker required: handle_message() is exercised directly
with fake retained-message payloads (the actual paho-mqtt wiring -
connect/subscribe/background thread - is a thin, untested-here layer
on top, same "test the logic, not the I/O" split as
flash_controller.py's tests). A separate fake client_factory confirms
connect()/disconnect() drive a client object correctly without needing
a real broker either.

Run: python3 test_mqtt_discovery.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mqtt_discovery import BaseDiscovery  # noqa: E402

failures = []


def check(label, cond):
    status = "PASS" if cond else "FAIL"
    print("[{}] {}".format(status, label))
    if not cond:
        failures.append(label)


# --- handle_message: happy path ---
d = BaseDiscovery(broker="test.local")
d.handle_message(
    "greenthumb/A1B2C3/device_info",
    json.dumps({"version": "0.3.0", "device_name": "Tomato Base", "friendly_name": "Tomato Base", "base_id": "A1B2C3"}),
)
bases = d.known_bases()
check("handle_message() registers the base under its base_id", "A1B2C3" in bases)
check("handle_message() captures friendly_name", bases["A1B2C3"]["friendly_name"] == "Tomato Base")
check("handle_message() captures version", bases["A1B2C3"]["version"] == "0.3.0")

# --- friendly_name empty/missing falls back to base_id (mirrors the base's own fallback - see core/mqtt_client.py) ---
d = BaseDiscovery(broker="test.local")
d.handle_message("greenthumb/D4E5F6/device_info", json.dumps({"version": "0.3.0", "device_name": "", "friendly_name": ""}))
check("empty friendly_name falls back to base_id", d.known_bases()["D4E5F6"]["friendly_name"] == "D4E5F6")

d = BaseDiscovery(broker="test.local")
d.handle_message("greenthumb/D4E5F6/device_info", json.dumps({"version": "0.3.0"}))  # no friendly_name key at all
check("missing friendly_name key falls back to base_id", d.known_bases()["D4E5F6"]["friendly_name"] == "D4E5F6")

# --- a topic that doesn't match the expected shape is ignored, not raised ---
d = BaseDiscovery(broker="test.local")
d.handle_message("greenthumb/A1B2C3/state", json.dumps({"temp_f": 72}))  # a different topic, not device_info
check("non-device_info topic is ignored", d.known_bases() == {})

d.handle_message("some/other/topic/entirely", "{}")
check("a topic not matching greenthumb/<id>/device_info at all is ignored", d.known_bases() == {})

# --- malformed JSON is ignored, not raised ---
d = BaseDiscovery(broker="test.local")
try:
    d.handle_message("greenthumb/A1B2C3/device_info", "not valid json{{{")
    raised = False
except Exception:
    raised = True
check("malformed JSON payload doesn't raise", raised is False)
check("malformed JSON payload doesn't register a base", d.known_bases() == {})

# --- multiple bases accumulate independently ---
d = BaseDiscovery(broker="test.local")
d.handle_message("greenthumb/AAAAAA/device_info", json.dumps({"friendly_name": "Base One"}))
d.handle_message("greenthumb/BBBBBB/device_info", json.dumps({"friendly_name": "Base Two"}))
check("two different bases both accumulate", set(d.known_bases().keys()) == {"AAAAAA", "BBBBBB"})

# --- known_bases() returns a copy, not the live internal dict ---
d = BaseDiscovery(broker="test.local")
d.handle_message("greenthumb/AAAAAA/device_info", json.dumps({"friendly_name": "Base One"}))
snapshot = d.known_bases()
snapshot["AAAAAA"]["friendly_name"] = "TAMPERED"
check("known_bases() returns an independent copy - mutating it doesn't affect the live cache", d.known_bases()["AAAAAA"]["friendly_name"] == "Base One")


# --- connect()/disconnect() drive a fake client correctly ---
class FakeMqttClient:
    def __init__(self):
        self.on_connect = None
        self.on_message = None
        self.connected_to = None
        self.subscribed_to = None
        self.loop_started = False
        self.loop_stopped = False
        self.disconnected = False

    def connect(self, broker, port, keepalive=60):
        self.connected_to = (broker, port)

    def loop_start(self):
        self.loop_started = True
        # Simulate the broker accepting the connection, same shape
        # real paho-mqtt would call back with.
        if self.on_connect:
            self.on_connect(self, None, {}, 0, None)

    def loop_stop(self):
        self.loop_stopped = True

    def disconnect(self):
        self.disconnected = True

    def subscribe(self, topic, qos=0):
        self.subscribed_to = (topic, qos)


fake_client = FakeMqttClient()
d = BaseDiscovery(broker="test.local", port=1883, client_factory=lambda: fake_client)
d.connect()
check("connect() connects to the configured broker/port", fake_client.connected_to == ("test.local", 1883))
check("connect() starts the background loop", fake_client.loop_started is True)
check("on_connect (reason_code 0) subscribes to the discovery wildcard", fake_client.subscribed_to == ("greenthumb/+/device_info", 1))

d.disconnect()
check("disconnect() stops the loop", fake_client.loop_stopped is True)
check("disconnect() disconnects the client", fake_client.disconnected is True)

# --- connect() without a broker configured raises clearly rather than silently no-oping ---
d = BaseDiscovery(broker=None)
try:
    d.connect()
    raised_no_broker = False
except RuntimeError:
    raised_no_broker = True
check("connect() with no broker configured raises RuntimeError", raised_no_broker is True)

# --- set_broker() updates broker/port and disconnects any existing client first ---
fake_client2 = FakeMqttClient()
d = BaseDiscovery(broker="old.local", client_factory=lambda: fake_client2)
d.connect()
d.set_broker("new.local", 8883)
check("set_broker() disconnects the previous client", fake_client2.disconnected is True)
check("set_broker() updates the stored broker/port", (d._broker, d._port) == ("new.local", 8883))

print()
if failures:
    print("{} check(s) failed: {}".format(len(failures), failures))
    sys.exit(1)
else:
    print("All checks passed.")
