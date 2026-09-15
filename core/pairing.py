"""
core/pairing.py - BLE module pairing state machine, driven by the
button's long-press (drivers/button.py's on_long_press, 3s - distinct
from wifi.py's boot-hold setup trigger) or the pairing/command MQTT
topic (see docs/ARCHITECTURE.md - same command-driven pattern as
core/calibration.py: one state machine, two entry points).

Flow:
  start (button or MQTT) -> scanning (60s window, caller should show
  blinking blue via status_led.set_pairing_mode(True)) -> candidates
  found -> select (user confirms even if only one candidate was found -
  avoids silently pairing a neighbor's device) -> paired (registered via
  module_manager, mod_id assigned) -> acknowledge -> idle.
  No candidates found within the timeout -> idle directly.
  cancel is valid at any point, including mid-scan.

Depends on two collaborators NOT YET WRITTEN (interfaces documented
here, to be implemented to match):

  ble_central (core/ble_central.py):
    async scan(timeout_s) -> list of {"address": str, "type": str, "serial": str}
      Scans for BLE peripherals advertising the GreenThumb service UUID
      (see docs/ARCHITECTURE.md GATT schema) for up to timeout_s
      seconds, reading each candidate's Device Type AND Module Serial
      characteristics in one brief connection. Returns whatever was
      found when the window closes (possibly empty).

  module_manager (core/module_manager.py):
    register(address, mod_type, serial) -> mod_id
      Pure, synchronous registry operation - no BLE connection happens
      here (the serial was already read during scan(), specifically so
      this can stay synchronous and be called directly from select()
      below without needing this whole state machine to become async).
      Returns the same mod_id as before if this serial was already
      known (so a module keeps its identity across re-pairing after a
      reboot), otherwise assigns a fresh one. Persists the registry to
      flash.

This module owns only the pairing STATE MACHINE - it does not scan or
connect to BLE hardware itself and does not persist the module registry
itself. It also does not touch the status LED or publish MQTT directly -
the caller (main.py) is expected to call status_led.set_pairing_mode()
on entering/leaving the scanning state and publish status() after every
transition, same separation-of-concerns pattern as calibration.py.

start() is deliberately synchronous - it launches the scan as a
background asyncio task rather than awaiting it inline, so
handle_command() can dispatch every action (including "start")
uniformly and synchronously, and so button.py's synchronous
on_long_press callback can call start() directly with no async wrapper.
"""

import asyncio


class PairingManager:
    def __init__(self, ble_central, module_manager, timeout_s=60):
        self._ble_central = ble_central
        self._module_manager = module_manager
        self._timeout_s = timeout_s
        self._state = "idle"
        self._candidates = []
        self._paired_result = None  # {"mod_id":..., "type":...} after a successful pair

    def status(self):
        """Current state as a dict, matching the pairing/status MQTT payload shape."""
        if self._state == "paired":
            return {"state": "paired", "mod_id": self._paired_result["mod_id"], "type": self._paired_result["type"]}
        if self._state == "scanning":
            return {"state": "scanning", "candidates": self._candidates}
        if self._state == "awaiting_select":
            return {"state": "awaiting_select", "candidates": self._candidates}
        return {"state": self._state}

    def start(self):
        """
        Begins a pairing session. Returns False if not currently idle
        (a fresh start must cancel() first). Launches the scan as a
        background task - this method returns immediately, state
        becomes "scanning" right away.
        """
        if self._state != "idle":
            return False
        self._state = "scanning"
        self._candidates = []
        asyncio.create_task(self._run_scan())
        return True

    async def _run_scan(self):
        candidates = await self._ble_central.scan(self._timeout_s)
        if self._state != "scanning":
            return  # cancelled while scanning - discard stale results
        self._candidates = candidates
        self._state = "awaiting_select" if candidates else "idle"

    def select(self, address):
        """
        Confirms which discovered candidate to pair. Always required,
        even with only one candidate found.
        """
        if self._state != "awaiting_select":
            return False
        match = None
        for c in self._candidates:
            if c["address"] == address:
                match = c
                break
        if match is None:
            return False

        mod_id = self._module_manager.register(address, match["type"], match["serial"])
        self._paired_result = {"mod_id": mod_id, "type": match["type"]}
        self._state = "paired"
        return True

    def cancel(self):
        """
        Valid at any point, including mid-scan (the in-flight scan task
        checks state on completion and discards its results if this was
        called - see _run_scan).
        """
        self._state = "idle"
        self._candidates = []
        self._paired_result = None
        return True

    def acknowledge(self):
        """
        Call once the caller has published the "paired" status and no
        longer needs it held - returns to idle so a subsequent start()
        is accepted. A separate named action from cancel() so a
        successful pairing has its own explicit close-out step, rather
        than overloading cancel() (which reads as "something went
        wrong") for the success path too.
        """
        if self._state != "paired":
            return False
        self._state = "idle"
        self._candidates = []
        self._paired_result = None
        return True

    def handle_command(self, payload):
        """Dispatches a pairing/command MQTT payload (or an equivalent internal call)."""
        action = payload.get("action")
        if action == "start":
            return self.start()
        if action == "select":
            address = payload.get("address")
            if address is None:
                return False
            return self.select(address)
        if action == "cancel":
            return self.cancel()
        if action == "acknowledge":
            return self.acknowledge()
        return False
