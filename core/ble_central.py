"""
core/ble_central.py - BLE central role: scanning for GreenThumb modules
and raw GATT connection primitives (read/write/notify, MTU negotiation
with chunked-fragment fallback).

Uses aioble (vendored into /lib/aioble - not reproduced here, install
via mip or copy from the aioble GitHub repo).

CONFIDENCE NOTE: verified against aioble's actual source
(micropython-lib/micropython/bluetooth/aioble/aioble/device.py) - the
scan/connect/service/characteristic/read/write/subscribe API shape used
here matches, and exchange_mtu(mtu=None, timeout_ms=1000) plus the
connection.mtu attribute (set by the MTU-exchanged IRQ handler) are both
confirmed real, not assumed. No fallback-on-AttributeError guard needed
for that specific call anymore, though it's kept anyway (see
_negotiate_mtu) since a connection can still legitimately fail to
negotiate for other reasons (peripheral doesn't support it, timeout).

TESTING LIMITATION: this sandbox has no BLE hardware. Functional tests
for this module (see project notes) use a hand-written mock of aioble,
built to match its real, verified API shape - they confirm this
module's OWN logic (chunk splitting/reassembly math, candidate dedup,
JSON encode/decode, MTU fallback behavior) is correct. The aioble API
surface itself is now verified against its actual source, not just
memory - but real BLE radio behavior (timing, real peripherals'
quirks, actual negotiated MTU values in practice) can still only be
confirmed on real hardware.

Everything else here (which service/characteristics to look for, and
the chunked-fragment fallback protocol) is this project's own design
(see docs/ARCHITECTURE.md GATT schema), not aioble library specifics,
and is on much firmer ground.

Fulfills the ble_central.scan(timeout_s) contract that core/pairing.py
was built against (see that module's docstring).
"""

import aioble
import bluetooth

try:
    import ujson as json
except ImportError:
    import json

# From docs/ARCHITECTURE.md GATT schema
_SERVICE_UUID = bluetooth.UUID("a1e50000-b5a3-4393-b673-5d2a1d3d0001")
_CHAR_DEVICE_TYPE = bluetooth.UUID("a1e50000-b5a3-4393-b673-5d2a1d3d0002")
_CHAR_MODULE_SERIAL = bluetooth.UUID("a1e50000-b5a3-4393-b673-5d2a1d3d0003")
_CHAR_STATE = bluetooth.UUID("a1e50000-b5a3-4393-b673-5d2a1d3d0004")
_CHAR_COMMAND = bluetooth.UUID("a1e50000-b5a3-4393-b673-5d2a1d3d0005")
_CHAR_FIRMWARE_VERSION = bluetooth.UUID("a1e50000-b5a3-4393-b673-5d2a1d3d0006")

_DESIRED_MTU = 247       # standard ESP32 support ceiling, per docs/ARCHITECTURE.md
_CHUNK_SIZE = 19         # payload bytes per fragment (1 header byte in a 20-byte default MTU)
_CONNECT_TIMEOUT_MS = 5000


async def scan(timeout_s):
    """
    Scans for BLE peripherals advertising the GreenThumb service UUID
    for up to timeout_s seconds. For each one found, briefly connects to
    read its Device Type and Module Serial characteristics in that same
    connection, then disconnects. Returns whatever was found when the
    window closes - possibly empty.

    Reading the serial here (not later, during registration) keeps
    module_manager.register() a pure synchronous registry operation -
    see core/module_manager.py's docstring for why that matters.

    This is the exact contract core/pairing.py depends on
    (ble_central.scan(timeout_s) -> list of {"address", "type", "serial"}).
    """
    candidates = []
    seen_addresses = set()

    async with aioble.scan(
        timeout_s * 1000, interval_us=30000, window_us=30000, active=True
    ) as scanner:
        async for result in scanner:
            if _SERVICE_UUID not in result.services():
                continue
            address = _format_address(result.device)
            if address in seen_addresses:
                continue
            seen_addresses.add(address)

            info = await _read_candidate_info(result.device)
            if info is not None:
                candidates.append({"address": address, "type": info[0], "serial": info[1]})

    return candidates


async def _read_candidate_info(device):
    """
    Briefly connects to a discovered device solely to read its Device
    Type and Module Serial characteristics, then disconnects. Returns
    (mod_type, serial) tuple, or None on any failure - a candidate that
    can't report both during scanning is excluded rather than surfaced
    as a broken entry for the user to pick.
    """
    connection = None
    try:
        connection = await device.connect(timeout_ms=_CONNECT_TIMEOUT_MS)
        service = await connection.service(_SERVICE_UUID)

        type_char = await service.characteristic(_CHAR_DEVICE_TYPE)
        mod_type = (await type_char.read()).decode()

        serial_char = await service.characteristic(_CHAR_MODULE_SERIAL)
        serial = (await serial_char.read()).decode()

        return (mod_type, serial)
    except OSError:
        return None
    finally:
        if connection is not None:
            await connection.disconnect()


def _format_address(device):
    return ":".join("{:02X}".format(b) for b in device.addr)


def _chunk_payload(data):
    """
    Splits data into _CHUNK_SIZE-byte fragments, each prefixed with a
    1-byte sequence number (0xFF marks the final fragment), per the
    chunked-fragment protocol in docs/ARCHITECTURE.md. Pure function,
    no I/O - kept separate from _write_chunked so the splitting logic
    can be tested without a real BLE connection.
    """
    total_chunks = (len(data) + _CHUNK_SIZE - 1) // _CHUNK_SIZE
    if total_chunks == 0:
        total_chunks = 1  # even an empty payload gets one (final) fragment
    fragments = []
    for i in range(total_chunks):
        chunk = data[i * _CHUNK_SIZE: (i + 1) * _CHUNK_SIZE]
        seq = 0xFF if i == total_chunks - 1 else i
        fragments.append(bytes([seq]) + chunk)
    return fragments


def _reassemble_fragment(buffer, fragment):
    """
    Appends one fragment's payload (after its 1-byte header) to buffer.
    Returns (buffer, is_final). Pure function, no I/O.
    """
    seq = fragment[0]
    buffer += fragment[1:]
    return buffer, seq == 0xFF


class ModuleConnection:
    """
    Wraps a connected GreenThumb module: MTU negotiation (attempted once
    at connect), and read/write helpers that transparently fall back to
    the chunked-fragment protocol if MTU negotiation failed or wasn't
    large enough for a given payload.
    """

    def __init__(self, connection, service):
        self._connection = connection
        self._service = service
        self._mtu_negotiated = False

    @classmethod
    async def connect(cls, address_str):
        """
        address_str: "AA:BB:CC:DD:EE:FF" formatted address, as returned
        by scan(). Connects, discovers the GreenThumb service, attempts
        MTU negotiation, and returns a ready-to-use ModuleConnection.
        """
        addr_bytes = bytes(int(b, 16) for b in address_str.split(":"))
        device = aioble.Device(aioble.ADDR_PUBLIC, addr_bytes)
        connection = await device.connect(timeout_ms=_CONNECT_TIMEOUT_MS)
        service = await connection.service(_SERVICE_UUID)

        instance = cls(connection, service)
        instance._mtu_negotiated = await instance._negotiate_mtu()
        return instance

    async def _negotiate_mtu(self):
        """
        Confirmed against aioble source (see module docstring) -
        exchange_mtu(mtu=None, timeout_ms=1000) and the resulting .mtu
        attribute are both real. Still wrapped in try/except: a
        peripheral can legitimately fail to negotiate (doesn't support
        it, times out) even though the API call itself is correct, and
        that should degrade to chunking rather than propagate.
        """
        try:
            await self._connection.exchange_mtu(_DESIRED_MTU, timeout_ms=_CONNECT_TIMEOUT_MS)
            return self._connection.mtu >= _DESIRED_MTU
        except (OSError, ValueError):
            return False

    async def read_serial(self):
        char = await self._service.characteristic(_CHAR_MODULE_SERIAL)
        raw = await char.read()
        return raw.decode()

    async def read_firmware_version(self):
        char = await self._service.characteristic(_CHAR_FIRMWARE_VERSION)
        raw = await char.read()
        return raw.decode()

    async def write_command_json(self, payload_dict):
        """
        Encodes payload_dict as JSON and writes to the Command
        characteristic, chunking automatically if it exceeds what fits
        in the negotiated MTU (or falling back to the fixed chunk size
        if MTU negotiation failed).
        """
        data = json.dumps(payload_dict).encode()
        char = await self._service.characteristic(_CHAR_COMMAND)

        max_single_write = (self._connection.mtu - 3) if self._mtu_negotiated else 0
        if len(data) <= max_single_write:
            await char.write(data)
        else:
            for fragment in _chunk_payload(data):
                await char.write(fragment)

    async def subscribe_state(self, on_state):
        """
        Subscribes to the State characteristic's notifications and calls
        on_state(parsed_json_dict) each time a complete payload is
        reassembled. Distinguishes a single-fragment notification (MTU
        was sufficient - the whole JSON arrives in one notification)
        from the chunked-fragment fallback by checking the first byte:
        valid JSON always starts with '{' (0x7B) or '[' (0x5B), values
        far outside the 0-254 sequence-number / 0xFF-final range for
        anything but a many-hundred-fragment payload, which state
        updates never reach in practice.

        Assumes the peripheral sends one complete payload at a time
        (doesn't interleave fragments from two different updates) -
        reasonable for module firmware publishing one state snapshot
        per notification cycle.
        """
        char = await self._service.characteristic(_CHAR_STATE)
        await char.subscribe(notify=True)

        buffer = bytearray()
        async for raw in char.notified():
            if raw[0] in (0x7B, 0x5B):
                on_state(json.loads(raw))
                buffer = bytearray()
                continue
            buffer, is_final = _reassemble_fragment(buffer, raw)
            if is_final:
                on_state(json.loads(bytes(buffer)))
                buffer = bytearray()

    async def disconnect(self):
        await self._connection.disconnect()
