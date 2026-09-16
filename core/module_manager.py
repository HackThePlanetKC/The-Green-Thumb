"""
core/module_manager.py - Persisted module registry and BLE<->MQTT relay
for already-paired BLE modules.

Registry keyed by BLE serial (read from each module's Module Serial
characteristic during ble_central.scan() - see docs/ARCHITECTURE.md
GATT schema), not by BLE address. Serial is the one thing guaranteed
stable across a module reboot (and, less commonly, an address change,
since some BLE stacks rotate random addresses); address alone would let
a rebooted module come back as a "new" module. mod_id is a short
derived identifier assigned once per serial and never reused, even if
the module is temporarily unpaired.

Fulfills the module_manager.register(address, mod_type, serial) -> mod_id
contract that core/pairing.py was built against (see that module's
docstring). This is deliberately a PURE, SYNCHRONOUS function - no BLE
connection happens here. The serial is read once during
ble_central.scan() (in the same brief connection that already reads
Device Type), specifically so register() can be called directly from
pairing.py's synchronous select() without needing that whole state
machine to become async. An earlier version of this design had
register() reading the serial itself over BLE, which would have forced
pairing.py's select()/handle_command() to become async - fixed before
either module was used together, once the mismatch surfaced here.

This module also owns the ongoing relay for already-paired modules:
reconnecting after a reboot, subscribing to each module's State
characteristic and republishing to MQTT, and relaying MQTT commands
down to a module via BLE. Registration and relay are different concerns
sharing one registry, kept in one file since they're small and tightly
coupled - the relay only ever operates on modules the registry already
knows about.

KNOWN SIMPLIFICATIONS (not yet handled, flagged rather than silently
glossed over):
- No enforcement of the ESP32's concurrent BLE connection limit
  (~3-9 connections per docs/ARCHITECTURE.md) - relay_forever() below
  attempts to connect to every registered module without any queuing
  or backpressure once that count is exceeded.
- Reconnect backoff per module is a fixed retry delay, not the capped
  exponential pattern used elsewhere (wifi.py, ntp.py, mqtt_client.py) -
  fine for a small number of modules, but not yet given the same
  treatment.
- on_module_health (a hook for module-reported problems, e.g. "reservoir
  empty", to feed into the base's overall health aggregation) is an
  unused parameter - no real module type exists yet to define what a
  health contribution even looks like.
"""

import asyncio

import storage

_REGISTRY_PATH = "/module_registry.json"
_RELAY_RETRY_DELAY_S = 30


class ModuleManager:
    def __init__(self, ble_central_module, mqtt_client, on_module_health=None):
        """
        ble_central_module: the core/ble_central module itself, for its
        ModuleConnection.connect(address) classmethod - used by the
        relay, not by register() (see module docstring).
        mqtt_client: the GreenThumbMqtt instance, for
        publish_module_state/publish_module_status.
        on_module_health: see KNOWN SIMPLIFICATIONS above - accepted
        but not yet wired to anything real.
        """
        self._ble_central = ble_central_module
        self._mqtt = mqtt_client
        self._on_module_health = on_module_health
        self._registry = self._load_registry()
        self._connections = {}  # mod_id -> ModuleConnection, for currently-relayed modules
        self._last_state = {}  # mod_id -> last-received State dict, see get_last_state()

    def _load_registry(self):
        data = storage.read_json(_REGISTRY_PATH)
        return data if data is not None else {}

    def _save_registry(self):
        storage.write_json(_REGISTRY_PATH, self._registry)

    def register(self, address, mod_type, serial):
        """
        Looks up or assigns a mod_id for `serial`, updates its recorded
        address/type (either could have changed since last time - e.g.
        the module reporting a firmware-updated type string), persists
        the registry, and returns the mod_id. See module docstring for
        why this never touches BLE.
        """
        if serial in self._registry:
            mod_id = self._registry[serial]["mod_id"]
        else:
            mod_id = self._generate_mod_id(serial)

        self._registry[serial] = {
            "mod_id": mod_id,
            "address": address,
            "type": mod_type,
        }
        self._save_registry()
        return mod_id

    def _generate_mod_id(self, serial):
        """
        Derives a short, stable mod_id from the serial itself (not a
        counter) - so re-registering the exact same physical module
        after, say, a flash wipe of the registry still produces the
        same mod_id, matching the "stable identity" goal described in
        the module docstring even in that edge case. Uses a simple
        sum-based hash of the serial string - collisions are
        theoretically possible with enough modules, but implausible at
        the scale this project operates at (a handful of modules per
        base station).
        """
        h = 0
        for ch in serial:
            h = (h * 31 + ord(ch)) & 0xFFFFFF
        return "{:06X}".format(h)

    def known_module_ids(self):
        """Returns all currently-registered mod_ids."""
        return [entry["mod_id"] for entry in self._registry.values()]

    def is_module_online(self, mod_id):
        """
        True if this module currently has an active relay connection.
        Used by main.py's dashboard state_provider to report per-module
        online/offline status.
        """
        return mod_id in self._connections

    def get_last_state(self, mod_id):
        """
        Returns the last-received State dict for a module, or None if it
        was never received (e.g. not connected yet - relay_forever()
        picks up newly-paired modules on a 5s cycle, so this can
        legitimately be None for a short window right after pairing).

        This is a passthrough cache - module_manager.py doesn't
        interpret any fields in the state dict itself (e.g.
        "needs_calibration", "calibration_fields" - see
        docs/ARCHITECTURE.md's provisional module calibration contract).
        Whatever the module's own firmware puts in its State JSON is
        exactly what callers get back here.
        """
        return self._last_state.get(mod_id)

    def _resolve_name_collision(self, mod_id, state_dict):
        """
        Called once, on a module's FIRST-ever State report (see on_state
        above) - checks its self-reported "name" against every OTHER
        currently-known module's cached name, and if it collides
        exactly, pushes a disambiguated name ("Water Pump 2", "Water
        Pump 3", ...) back to the module via the same generic Command
        channel calibration values use - {"action": "set_name", "value": ...}.
        Only ever runs at first-contact, not on every subsequent state
        update, so it can't fight a name the user (or the module itself)
        deliberately changes later - see docs/ARCHITECTURE.md.

        A module that adopts the pushed name is expected to report it
        back via its own State "name" field on its next update - this
        method doesn't update self._last_state itself, since it has no
        way to know whether the module actually accepted the push.
        """
        name = state_dict.get("name")
        if not name:
            return  # nothing to disambiguate if the module didn't report a name at all

        other_names = {
            other_id: (other_state or {}).get("name")
            for other_id, other_state in self._last_state.items()
            if other_id != mod_id
        }
        if name not in other_names.values():
            return  # no collision, nothing to do

        suffix = 2
        while "{} {}".format(name, suffix) in other_names.values():
            suffix += 1
        disambiguated = "{} {}".format(name, suffix)

        # Optimistically update the cache immediately, before the module
        # has confirmed adopting the pushed name - closes a real race
        # window where a second AND third module connecting in quick
        # succession (both still seeing the same stale, not-yet-
        # disambiguated cached name for the other) could otherwise both
        # be pushed the identical disambiguated name, still colliding.
        # Self-corrects if the module's actual next report differs (e.g.
        # it ignored the push) - this is a cache update, not a republish.
        self._last_state[mod_id]["name"] = disambiguated

        asyncio.create_task(self.send_command(mod_id, {"action": "set_name", "value": disambiguated}))

    def get_by_mod_id(self, mod_id):
        """Returns the registry entry (dict with address/type/mod_id) for a mod_id, or None."""
        for entry in self._registry.values():
            if entry["mod_id"] == mod_id:
                return entry
        return None

    def unregister(self, mod_id):
        """
        Removes a module from the registry entirely (not the same as a
        transient disconnect - this is a deliberate "forget this
        module" action). Returns True if it was found and removed.
        """
        for serial, entry in list(self._registry.items()):
            if entry["mod_id"] == mod_id:
                del self._registry[serial]
                self._save_registry()
                return True
        return False

    async def relay_forever(self):
        """
        Background asyncio task: maintains a BLE connection to every
        registered module, relaying State notifications to MQTT and
        publishing online/offline status. Launches one independent
        per-module task and lets them run concurrently - see KNOWN
        SIMPLIFICATIONS above regarding the ESP32 connection limit,
        which this does not enforce.
        """
        tasks = {}
        while True:
            for mod_id in self.known_module_ids():
                if mod_id not in tasks or tasks[mod_id].done():
                    tasks[mod_id] = asyncio.create_task(self._relay_one_module(mod_id))
            await asyncio.sleep(5)

    async def _relay_one_module(self, mod_id):
        """
        Connects to one module and relays its State notifications to
        MQTT until disconnected, then retries after a fixed delay. Runs
        forever (or until the module is unregistered - checked each
        retry loop, so an unregister() takes effect within one retry
        cycle rather than needing this task killed externally).
        """
        while True:
            entry = self.get_by_mod_id(mod_id)
            if entry is None:
                return  # unregistered - stop relaying

            try:
                connection = await self._ble_central.ModuleConnection.connect(entry["address"])
                self._connections[mod_id] = connection
                self._mqtt.publish_module_status(mod_id, online=True)

                def on_state(state_dict):
                    is_first_report = mod_id not in self._last_state
                    self._last_state[mod_id] = state_dict
                    self._mqtt.publish_module_state(mod_id, state_dict)

                    if is_first_report:
                        self._resolve_name_collision(mod_id, state_dict)

                await connection.subscribe_state(on_state)
            except OSError:
                pass
            finally:
                self._connections.pop(mod_id, None)
                self._mqtt.publish_module_status(mod_id, online=False)

            await asyncio.sleep(_RELAY_RETRY_DELAY_S)

    async def send_command(self, mod_id, payload_dict):
        """
        Relays an MQTT module/<mod_id>/command payload down to the
        module over BLE, using its currently-active relay connection.
        Returns False if the module isn't currently connected (caller
        has no ack channel to report this beyond that - consistent with
        every other command path in this project, see docs/ARCHITECTURE.md).
        """
        connection = self._connections.get(mod_id)
        if connection is None:
            return False
        try:
            await connection.write_command_json(payload_dict)
            return True
        except OSError:
            return False
