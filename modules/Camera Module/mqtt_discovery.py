"""
mqtt_discovery.py - Base discovery via the greenthumb/+/device_info
MQTT wildcard.

Subscribes to every base's retained device_info topic (see
core/mqtt_client.py's publish_device_info() in the base firmware repo)
to build a live list of known bases without any pre-configured base
list - a base just needs to have been online once for this module to
see it (device_info is retained, so it survives that base going
offline afterward).

Uses paho-mqtt - the standard Pi/Linux MQTT client library - not a
hand-rolled client like the base's own umqtt.simple usage. That choice
there is a MicroPython constraint (no full-featured MQTT client ships
with it); no such constraint exists on Pi/Linux, so pulling in the
standard, well-tested library is the natural platform fit, same
reasoning as ring.py's rpi_ws281x choice. CallbackAPIVersion.VERSION2
is pinned explicitly rather than left at the (deprecated,
warning-emitting) VERSION1 default.

friendly_name/base_id parsing here is the reason core/mqtt_client.py's
friendly_name field exists at all (see docs/ARCHITECTURE.md) - this
module needs a human-readable label for the association picker instead
of a raw MAC-derived base_id.
"""

import json
import re

try:
    import paho.mqtt.client as mqtt
except ImportError:  # only required on the real Pi (pip install paho-mqtt)
    mqtt = None

_DEVICE_INFO_TOPIC_RE = re.compile(r"^greenthumb/([^/]+)/device_info$")
_DISCOVERY_TOPIC = "greenthumb/+/device_info"


class BaseDiscovery:
    """
    Maintains a live dict of known bases: {base_id: {"friendly_name":
    str, "device_name": str, "version": str}}, keyed by base_id -
    never friendly_name, since friendly_name is a display-only,
    user-editable label that can change at any time (see
    base_association.py, which stores associations by base_id for the
    same reason).

    Message-processing logic (handle_message) is deliberately separate
    from the actual paho-mqtt wiring (connect/subscribe/the background
    thread) so it's directly testable with fake retained messages, no
    real broker required - see tests/test_mqtt_discovery.py.
    """

    def __init__(self, broker, port=1883, client_id="greenthumb-camera-discovery", client_factory=None):
        self._broker = broker
        self._port = port
        self._client_id = client_id
        self._bases = {}
        self._client_factory = client_factory or self._make_real_client
        self._client = None

    def known_bases(self):
        """
        Returns a copy - {base_id: {...}} - callers can't accidentally
        mutate the live cache. Copies each per-base dict individually,
        not just the outer dict: a plain dict(self._bases) is a
        shallow copy, which would still share the same inner dicts
        with the live cache - mutating a returned entry's fields would
        silently corrupt handle_message()'s own state. One level of
        per-entry copying is enough (never a deeper structure, given
        this method's own known, fixed shape).
        """
        return {base_id: dict(info) for base_id, info in self._bases.items()}

    def handle_message(self, topic, payload):
        """
        Processes one retained (or live) device_info message. A topic
        that doesn't match the expected shape, or malformed JSON, is
        ignored rather than raised - same "never crash on a bad
        message" posture as core/mqtt_client.py's own _on_message.

        Doesn't track online/offline here - device_info carries no
        liveness signal (that's the separate `status` LWT topic, not
        subscribed by this module); a base that's gone offline stays
        in known_bases() until this module restarts, which is fine for
        an association picker (you're picking which base to monitor,
        not checking whether it's currently reachable).
        """
        match = _DEVICE_INFO_TOPIC_RE.match(topic)
        if not match:
            return
        base_id = match.group(1)

        try:
            data = json.loads(payload)
        except (ValueError, TypeError):
            return

        self._bases[base_id] = {
            "friendly_name": data.get("friendly_name") or base_id,
            "device_name": data.get("device_name"),
            "version": data.get("version"),
        }

    def set_broker(self, broker, port=1883):
        """
        Updates broker connection details - mirrors
        core/mqtt_client.py's own set_broker()/connect() split in the
        base firmware repo. Disconnects first if currently connected;
        caller is responsible for calling connect() again afterward.
        """
        self.disconnect()
        self._broker = broker
        self._port = port

    def connect(self):
        """Connects and subscribes to the discovery wildcard - real broker I/O, not covered by handle_message's own tests."""
        if not self._broker:
            raise RuntimeError("no broker configured - call set_broker() first")

        self._client = self._client_factory()
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.connect(self._broker, self._port, keepalive=60)
        self._client.loop_start()

    def disconnect(self):
        if self._client is not None:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None

    def _make_real_client(self):
        if mqtt is None:
            raise RuntimeError(
                "paho-mqtt is not installed - this only runs with the real "
                "library present (pip install paho-mqtt). See this module's BUILD.md."
            )
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self._client_id)

    def _on_connect(self, client, userdata, connect_flags, reason_code, properties):
        if reason_code == 0:
            client.subscribe(_DISCOVERY_TOPIC, qos=1)

    def _on_message(self, client, userdata, message):
        payload = message.payload.decode() if isinstance(message.payload, bytes) else message.payload
        self.handle_message(message.topic, payload)
