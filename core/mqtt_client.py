"""
core/mqtt_client.py - MQTT client wrapper: topic builder, publish/subscribe,
LWT, and the generic set_config command handler.

Uses plain umqtt.simple (NOT umqtt.robust, despite earlier project notes -
see below), vendored into /lib. umqtt.simple's connect()/publish() calls
are blocking, but short (small JSON payloads on a local network) - an
accepted tradeoff, same as core/wifi.py's connect_sta() and
core/ntp.py's sync_once(). check_msg() is umqtt.simple's non-blocking
poll variant (returns immediately whether or not a message arrived) -
this is the correct method for the asyncio polling loop below; wait_msg()
would block and is deliberately not used here.

DEVIATION FROM EARLIER PLAN: umqtt.robust was the originally vendored
choice (see docs/ARCHITECTURE.md history) for its automatic reconnect.
On closer inspection, umqtt.robust's reconnect works by catching OSError
and blocking in an internal retry loop until the broker comes back -
which would stall the entire asyncio event loop (display, sensors, BLE)
during a broker outage. That's inconsistent with wifi.py and ntp.py,
which both deliberately built their own async, non-blocking
connect/backoff logic rather than trusting a library's blocking retry.
This module does the same: plain umqtt.simple, with reconnect_forever()
below handling backoff asynchronously instead.

set_config with an invalid/unknown path is rejected silently - there is
no ack/error topic anywhere in this design (see docs/ARCHITECTURE.md),
so the implicit signal to the caller (HA) is that the retained config
topic simply does not change. This resolves the previously-open question
of what happens on an invalid set_config path.
"""

import asyncio

try:
    import ujson as json
except ImportError:
    import json

from umqtt.simple import MQTTClient

import config as config_module
import identity

TOPIC_PREFIX = "greenthumb"
_MAX_BACKOFF_S = 60


class GreenThumbMqtt:
    def __init__(
        self,
        broker,
        port,
        cfg,
        username="",
        password="",
        on_command=None,
        on_calibration_command=None,
        on_pairing_command=None,
        on_module_command=None,
        on_get_light_hours_today=None,
    ):
        """
        cfg: the live config dict (as returned by config.load()). This
        client mutates it in place via the generic set_config command
        and calls config.save() after a successful change, so every
        other task sharing the same dict object sees updates immediately.

        on_command / on_calibration_command / on_pairing_command are
        callables invoked for command actions OTHER than "set_config"
        (which this module handles directly). on_module_command(mod_id,
        payload) handles module/<mod_id>/command specifically.
        on_get_light_hours_today: zero-argument callable returning
        {"light_hours_today": float or None} (see
        core/light_tracker.py's get_light_hours_today()) - called on
        {"action": "get_light_hours_today"}, mirroring the web
        dashboard's on-demand refresh button so HA can request a fresh
        reading the same way, rather than waiting for the next periodic
        state publish.
        """
        self._base_id = identity.get_base_id()
        self._cfg = cfg
        self._on_command = on_command
        self._on_calibration_command = on_calibration_command
        self._on_pairing_command = on_pairing_command
        self._on_module_command = on_module_command
        self._on_get_light_hours_today = on_get_light_hours_today

        client_id = "greenthumb-{}".format(self._base_id)
        self._client = MQTTClient(
            client_id, broker, port=port,
            user=username or None, password=password or None,
            keepalive=60,
        )
        self._client.set_last_will(self._topic("status"), b"offline", retain=True, qos=1)
        self._client.set_callback(self._on_message)

        self._connected = False
        self._backoff_s = 1

    def _topic(self, suffix):
        return "{}/{}/{}".format(TOPIC_PREFIX, self._base_id, suffix)

    async def connect(self):
        """
        Connects, publishes "online" (retained), subscribes to all
        command topics, and publishes the current config snapshot so HA
        has current values immediately without waiting for a change.
        Blocking under the hood (see module docstring) but expected to
        complete quickly on a local network.
        """
        self._client.connect()
        self._connected = True
        self._backoff_s = 1

        self._client.publish(self._topic("status"), b"online", retain=True, qos=1)
        self._client.subscribe(self._topic("command"), qos=1)
        self._client.subscribe(self._topic("calibration/command"), qos=1)
        self._client.subscribe(self._topic("pairing/command"), qos=1)
        self._client.subscribe(self._topic("module/+/command"), qos=1)
        self.publish_config()

    def is_connected(self):
        return self._connected

    def set_broker(self, broker, port, username="", password=""):
        """
        Updates broker connection details at runtime and rebuilds the
        underlying MQTTClient - used by the settings page after saving
        new broker config, mirroring wifi.py's set_credentials()
        pattern. Marks disconnected; caller should await connect()
        immediately after to attempt a live reconnect (same "try it now
        and report success/failure" UX as the WiFi setup flow), and
        reconnect_forever() (if running as a background task) will also
        pick up the new settings on its next attempt regardless.
        """
        try:
            self._client.disconnect()
        except OSError:
            pass  # old client may never have connected in the first place - not an error worth surfacing here

        client_id = "greenthumb-{}".format(self._base_id)
        self._client = MQTTClient(
            client_id, broker, port=port,
            user=username or None, password=password or None,
            keepalive=60,
        )
        self._client.set_last_will(self._topic("status"), b"offline", retain=True, qos=1)
        self._client.set_callback(self._on_message)
        self._connected = False
        self._backoff_s = 1

    async def reconnect_forever(self):
        """
        Background asyncio task: if disconnected, retries connect() with
        capped exponential backoff (1s, 2s, 4s... capped at 60s) rather
        than hammering the broker or blocking indefinitely. Same pattern
        as wifi.py's connection retry and ntp.py's resync backoff.
        """
        while True:
            if not self._connected:
                try:
                    await self.connect()
                except OSError:
                    self._connected = False
                    wait_s = self._backoff_s
                    self._backoff_s = min(self._backoff_s * 2, _MAX_BACKOFF_S)
                    await asyncio.sleep(wait_s)
                    continue
            await asyncio.sleep(5)  # already connected - just check back periodically

    async def listen_forever(self, poll_interval_ms=200):
        """
        Background asyncio task: polls for incoming messages via
        check_msg() (non-blocking - returns immediately whether or not a
        message was pending), dispatching through the callback registered
        in __init__. Yields to the event loop between polls via
        asyncio.sleep_ms so this never starves other tasks.
        """
        while True:
            if self._connected:
                try:
                    self._client.check_msg()
                except OSError:
                    self._connected = False
                    # reconnect_forever() (running as a separate task)
                    # picks this up and handles reconnection with backoff.
            await asyncio.sleep_ms(poll_interval_ms)

    def publish_state(self, state_dict):
        self._publish_json("state", state_dict, retain=True)

    def publish_health(self, health_dict):
        self._publish_json("health", health_dict, retain=True)

    def publish_config(self):
        """Publishes the full current config as the retained config topic."""
        self._publish_json("config", self._cfg, retain=True)

    def publish_light_summary(self, summary_dict):
        self._publish_json("light_summary", summary_dict, retain=True)

    def publish_light_hours_today(self, data_dict):
        """
        Published on-demand in response to {"action": "get_light_hours_today"}
        (see __init__'s on_get_light_hours_today) - retained so a
        newly-subscribing HA sensor immediately sees the last requested
        value rather than "unavailable" until the next request.
        """
        self._publish_json("light_hours_today", data_dict, retain=True)

    def publish_calibration_status(self, status_dict):
        self._publish_json("calibration/status", status_dict, retain=True)

    def publish_pairing_status(self, status_dict):
        self._publish_json("pairing/status", status_dict, retain=True)

    def publish_module_state(self, mod_id, state_dict):
        self._publish_json("module/{}/state".format(mod_id), state_dict, retain=True)

    def publish_module_status(self, mod_id, online):
        payload = b"online" if online else b"offline"
        self._client.publish(
            self._topic("module/{}/status".format(mod_id)), payload, retain=True, qos=1
        )

    def _publish_json(self, suffix, data, retain=False):
        payload = json.dumps(data)
        self._client.publish(self._topic(suffix), payload, retain=retain, qos=1)

    def _on_message(self, topic, msg):
        topic = topic.decode() if isinstance(topic, bytes) else topic
        try:
            payload = json.loads(msg)
        except ValueError:
            return  # malformed JSON - silently ignored, no ack channel to report it

        module_prefix = self._topic("module/")

        if topic == self._topic("command"):
            self._handle_base_command(payload)
        elif topic == self._topic("calibration/command"):
            if self._on_calibration_command:
                self._on_calibration_command(payload)
        elif topic == self._topic("pairing/command"):
            if self._on_pairing_command:
                self._on_pairing_command(payload)
        elif topic.startswith(module_prefix) and topic.endswith("/command"):
            mod_id = topic[len(module_prefix):-len("/command")]
            if self._on_module_command:
                self._on_module_command(mod_id, payload)

    def _handle_base_command(self, payload):
        action = payload.get("action")
        if action == "set_config":
            self._handle_set_config(payload)
        elif action == "get_light_hours_today":
            self._handle_get_light_hours_today()
        elif self._on_command:
            self._on_command(payload)

    def _handle_get_light_hours_today(self):
        if self._on_get_light_hours_today is None:
            return  # no light_tracker wired up yet (main.py doesn't exist) - silently no-op, same as any unhandled command
        data = self._on_get_light_hours_today()
        self.publish_light_hours_today(data)

    def _handle_set_config(self, payload):
        path = payload.get("path")
        value = payload.get("value")
        if path is None:
            return
        try:
            config_module.set_by_path(self._cfg, path, value)
        except KeyError:
            return  # invalid/unknown path - rejected silently, see module docstring
        config_module.save(self._cfg)
        self.publish_config()
