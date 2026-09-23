"""
mqtt_presence.py - The Camera Module's own MQTT/HA presence: a
minimal top-level "global settings" device, plus per-base settings
relayed into each associated base's existing namespace.

Two distinct topic trees, deliberately isolated from each other:

  greenthumb/camera/<camera_id>/global/...
      Module-global settings ONLY (capture frequency, grid layout/
      assignment, flash on/off + threshold - see config.py). Creates
      one minimal HA device card for the camera module itself. Per-
      base health/metric data must NEVER appear here (item 14) - this
      module doesn't publish any capture/health data at all yet
      (capture scheduling and the health classifier aren't built, see
      README.md "Remaining"), so today that's automatically true, but
      it's worth stating as a hard rule for whoever builds that next:
      that data belongs under a base's own topic tree, not this one.

  greenthumb/<base_id>/module/<camera_id>/...
      Per-base settings (the metric opt-in toggles, see
      per_base_settings.py) for each base this module is associated
      with, published into that base's own existing HA device (item
      6/14: settings live there, "attached to each base's own HA
      device, per existing design"). Reuses the base station's own
      documented module/<mod_id>/status|state|command topic shape
      (see docs/ARCHITECTURE.md) - normally used for BLE-paired
      modules the base relays on behalf of, but nothing about the
      topic shape itself requires the BASE to be the one publishing
      it. This module has its own direct MQTT connection (unlike a
      BLE module, see this module's own README.md), so it publishes
      into that same shape itself, with camera_id standing in for
      mod_id. See decisions-and-practices.md for the base-firmware-
      side gap this doesn't (and isn't scoped to) close: the base's
      own LOCAL portal has no UI for this yet, even though the MQTT
      data and HA entities are there.

A second, separate paho-mqtt client connection from mqtt_discovery.py's
own - not one shared client. Two connections from one process is a
real (minor) resource cost; the alternative (extending BaseDiscovery,
which is deliberately scoped to "learn what bases exist", to also own
settings publishing/command handling) would mix two independent
responsibilities into one class and its one test file. Simplicity now,
revisit only if connection overhead is ever a measured problem on a Pi
Zero 2 W - not guessed at ahead of time.

Message-processing (handle_message/handle_global_command/
handle_base_command) is separate from the real paho-mqtt wiring, same
split as mqtt_discovery.py, so it's directly testable with fake
messages and no real broker - see tests/test_mqtt_presence.py.
"""

import json
import re

import version
from config import set_by_path

try:
    import paho.mqtt.client as mqtt
except ImportError:  # only required on the real Pi (pip install paho-mqtt)
    mqtt = None

TOPIC_PREFIX = "greenthumb"

# The only config.py top-level keys the global/command topic is ever
# allowed to touch. Anything else (wifi, mqtt broker credentials,
# associated_base_ids, per_base_settings) is rejected before
# set_by_path() is even called - a global HA command channel must
# never be able to rewrite this module's own WiFi/broker credentials
# or association list, only the module-global settings it's actually
# meant to expose.
_GLOBAL_WHITELIST = ("capture", "grid", "flash")


class CameraMqttPresence:
    def __init__(
        self, config_module, association, per_base_settings, camera_id,
        broker=None, port=1883, client_id=None, client_factory=None,
    ):
        """
        config_module: exposes load()/save() - see config.py. Same
        injection pattern as base_association.py, for the same test-
        isolation reason (tests point this at a throwaway file).
        association: a BaseAssociationManager (or anything exposing
        associated_base_ids()) - used to know which bases' topic trees
        to publish/subscribe into.
        per_base_settings: a PerBaseSettingsManager - source of truth
        for each base's settings snapshot, and the target of any
        set_metric command received on a base's command topic.
        """
        self._config_module = config_module
        self._association = association
        self._per_base_settings = per_base_settings
        self._camera_id = camera_id
        self._broker = broker
        self._port = port
        self._client_id = client_id or "greenthumb-camera-{}".format(camera_id)
        self._client_factory = client_factory or self._make_real_client
        self._client = None
        self._synced_base_ids = set()
        self._base_command_re = re.compile(
            r"^{}/([^/]+)/module/{}/command$".format(TOPIC_PREFIX, re.escape(camera_id))
        )

    def _global_topic(self, suffix):
        return "{}/camera/{}/global/{}".format(TOPIC_PREFIX, self._camera_id, suffix)

    def _base_topic(self, base_id, suffix):
        return "{}/{}/module/{}/{}".format(TOPIC_PREFIX, base_id, self._camera_id, suffix)

    def _global_module_settings(self):
        """The module-global-only subset of config.py published under global/config (item 13)."""
        cfg = self._config_module.load()
        return {"capture": cfg["capture"], "grid": cfg["grid"], "flash": cfg["flash"]}

    # --- real MQTT wiring ---

    def set_broker(self, broker, port=1883):
        self.disconnect()
        self._broker = broker
        self._port = port

    def connect(self):
        if not self._broker:
            raise RuntimeError("no broker configured - call set_broker() first")

        self._client = self._client_factory()
        self._client.will_set(self._global_topic("status"), b"offline", retain=True, qos=1)
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
            self._bootstrap()

    def _on_message(self, client, userdata, message):
        payload = message.payload.decode() if isinstance(message.payload, bytes) else message.payload
        self.handle_message(message.topic, payload)

    def _bootstrap(self):
        """Everything published/subscribed fresh on every (re)connect - mirrors core/mqtt_client.py's connect()."""
        self._publish(self._global_topic("status"), b"online", retain=True)
        self._publish_json(self._global_topic("device_info"), {"version": version.VERSION, "camera_id": self._camera_id}, retain=True)
        self.publish_global_config()
        self._client.subscribe(self._global_topic("command"), qos=1)
        self._synced_base_ids = set()
        self.sync_associated_bases(self._association.associated_base_ids())

    # --- publishing ---

    def publish_global_config(self):
        self._publish_json(self._global_topic("config"), self._global_module_settings(), retain=True)

    def sync_associated_bases(self, base_ids):
        """
        (Re)publishes status/state and (re)subscribes command for every
        currently-associated base, and marks any base no longer
        associated as offline/unsubscribed - called once at bootstrap
        and again whenever the association list changes (see
        web_portal.py's save_association handler), so a base removed
        from association doesn't leave a stale "online" HA entity
        behind pointing at settings this module no longer relays.
        """
        base_ids = set(base_ids)
        if self._client is None:
            self._synced_base_ids = base_ids
            return

        for removed_base_id in self._synced_base_ids - base_ids:
            self._client.unsubscribe(self._base_topic(removed_base_id, "command"))
            self._publish(self._base_topic(removed_base_id, "status"), b"offline", retain=True)

        for base_id in base_ids:
            self._client.subscribe(self._base_topic(base_id, "command"), qos=1)
            self._publish(self._base_topic(base_id, "status"), b"online", retain=True)
            self.publish_base_state(base_id)

        self._synced_base_ids = base_ids

    def publish_base_state(self, base_id):
        """
        Re-publishes base_id's current settings snapshot to its
        state topic. Public (not just called internally from
        sync_associated_bases/_handle_base_command) - web_portal.py
        calls this directly after the camera portal edits a base's
        settings, so HA reflects the change without waiting for that
        base to send its own MQTT command.
        """
        if self._client is not None:
            self._publish_json(self._base_topic(base_id, "state"), self._per_base_settings.get_settings(base_id), retain=True)

    def _publish(self, topic, payload, retain=False, qos=1):
        self._client.publish(topic, payload, retain=retain, qos=qos)

    def _publish_json(self, topic, data, retain=False, qos=1):
        self._publish(topic, json.dumps(data), retain=retain, qos=qos)

    # --- message handling (real-broker-free, see tests/test_mqtt_presence.py) ---

    def handle_message(self, topic, payload):
        try:
            data = json.loads(payload)
        except (ValueError, TypeError):
            return

        if topic == self._global_topic("command"):
            self._handle_global_command(data)
            return

        match = self._base_command_re.match(topic)
        if match:
            self._handle_base_command(match.group(1), data)

    def _handle_global_command(self, payload):
        """
        Mirrors core/mqtt_client.py's generic set_config command
        handler shape ({"action": "set_config", "path": ..., "value":
        ...}), restricted to _GLOBAL_WHITELIST's top-level keys. An
        unknown/disallowed path, or any action other than set_config,
        is rejected silently - same "no ack/error topic, the retained
        config topic just doesn't change" contract as the base
        station's own handler.
        """
        if payload.get("action") != "set_config":
            return
        path = payload.get("path")
        value = payload.get("value")
        if path is None:
            return
        if path.split(".")[0] not in _GLOBAL_WHITELIST:
            return

        cfg = self._config_module.load()
        try:
            set_by_path(cfg, path, value)
        except KeyError:
            return
        self._config_module.save(cfg)
        if self._client is not None:
            self.publish_global_config()

    def _handle_base_command(self, base_id, payload):
        """{"action": "set_metric", "metric": ..., "enabled": ...} - the only command this topic accepts (a narrower shape than the global command's dotted-path setter, since a base can only ever toggle its own metric set, see per_base_settings.py)."""
        if payload.get("action") != "set_metric":
            return
        metric = payload.get("metric")
        enabled = payload.get("enabled")
        if metric is None or enabled is None:
            return
        try:
            self._per_base_settings.set_metric(base_id, metric, bool(enabled))
        except ValueError:
            return  # unknown/non-toggleable metric - rejected silently, same contract as above
        self.publish_base_state(base_id)
