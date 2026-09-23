"""
test_wifi_manager.py - stub-based tests for wifi_manager.WifiManager.

No real nmcli/NetworkManager required: a fake `run` callable stands in
for subprocess.run, returning canned CompletedProcess results.
Run: python3 test_wifi_manager.py
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wifi_manager import WifiManager  # noqa: E402

failures = []


def check(label, cond):
    status = "PASS" if cond else "FAIL"
    print("[{}] {}".format(status, label))
    if not cond:
        failures.append(label)


def cp(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class FakeRun:
    """Records every call and returns whatever's queued for that command's first token."""

    def __init__(self):
        self.calls = []
        self.responses = {}  # keyed by args[0:2] joined, e.g. "nmcli device"

    def queue(self, key, result):
        self.responses[key] = result

    def __call__(self, args, timeout_s=20):
        self.calls.append(args)
        for key, result in self.responses.items():
            if " ".join(args).startswith(key):
                return result
        return cp(returncode=1, stderr="no fake response queued")


# --- has_credentials / set_credentials ---
w = WifiManager("", "", run=FakeRun())
check("has_credentials() False for empty ssid", w.has_credentials() is False)
w.set_credentials("MyHomeWifi", "hunter2")
check("has_credentials() True after set_credentials()", w.has_credentials() is True)

# --- scan_networks: dedup, sorted by signal desc, hidden/escaped SSIDs handled ---
run = FakeRun()
run.queue("nmcli -t -f SSID,SIGNAL", cp(stdout="\n".join([
    "MyHomeWifi:80",
    "Neighbor:45",
    "MyHomeWifi:60",   # duplicate SSID, weaker signal - should NOT replace the stronger one
    ":30",              # hidden network (blank SSID) - should be skipped
    "Cafe\\:WiFi:20",   # SSID containing an escaped literal colon
])))
w = WifiManager("", "", run=run)
networks = w.scan_networks()
check("scan_networks() dedups, keeps strongest signal, sorted desc", networks[:2] == ["MyHomeWifi", "Neighbor"])
check("scan_networks() unescapes a literal colon in an SSID", "Cafe:WiFi" in networks)
check("scan_networks() skips a hidden (blank) SSID", "" not in networks)

run_fail = FakeRun()
run_fail.queue("nmcli -t -f SSID,SIGNAL", cp(returncode=1, stderr="no wifi hardware"))
w = WifiManager("", "", run=run_fail)
check("scan_networks() returns [] on nmcli failure rather than raising", w.scan_networks() == [])

# --- connect_sta: success / failure / no credentials ---
run = FakeRun()
run.queue("nmcli device wifi connect", cp(returncode=0))
w = WifiManager("MyHomeWifi", "hunter2", run=run)
check("connect_sta() returns True on nmcli success", w.connect_sta() is True)

run = FakeRun()
run.queue("nmcli device wifi connect", cp(returncode=1, stderr="wrong password"))
w = WifiManager("MyHomeWifi", "wrongpass", run=run)
check("connect_sta() returns False on nmcli failure", w.connect_sta() is False)

w = WifiManager("", "", run=FakeRun())
check("connect_sta() returns False with no credentials, without even calling nmcli", w.connect_sta() is False)

# --- is_connected / ip_address ---
run = FakeRun()
run.queue("nmcli -t -f DEVICE,STATE", cp(stdout="eth0:connected\nwlan0:connected\n"))
w = WifiManager("", "", iface="wlan0", run=run)
check("is_connected() True when iface's state is 'connected'", w.is_connected() is True)

run = FakeRun()
run.queue("nmcli -t -f DEVICE,STATE", cp(stdout="wlan0:disconnected\n"))
w = WifiManager("", "", iface="wlan0", run=run)
check("is_connected() False when iface's state is 'disconnected'", w.is_connected() is False)

run = FakeRun()
run.queue("nmcli -t -f IP4.ADDRESS", cp(stdout="IP4.ADDRESS[1]:192.168.1.42/24\n"))
w = WifiManager("", "", iface="wlan0", run=run)
check("ip_address() strips the /prefix from nmcli's output", w.ip_address() == "192.168.1.42")

run = FakeRun()
run.queue("nmcli -t -f IP4.ADDRESS", cp(stdout=""))
w = WifiManager("", "", iface="wlan0", run=run)
check("ip_address() returns None when nmcli reports no address", w.ip_address() is None)

# --- next_backoff_s: doubles, caps at 60 ---
w = WifiManager("", "", run=FakeRun())
seq = [w.next_backoff_s() for _ in range(8)]
check("next_backoff_s() doubles each call, capped at 60", seq == [1, 2, 4, 8, 16, 32, 60, 60])

# --- start_ap: builds the expected SSID, calls nmcli hotspot ---
run = FakeRun()
run.queue("nmcli device wifi hotspot", cp(returncode=0))
w = WifiManager("", "", run=run)
ssid = w.start_ap("ABC123")
check("start_ap() returns the expected SSID shape", ssid == "GreenThumb-Camera-Setup-ABC123")
check("start_ap() actually invoked nmcli hotspot", any("hotspot" in c for c in run.calls))

print()
if failures:
    print("{} check(s) failed: {}".format(len(failures), failures))
    sys.exit(1)
else:
    print("All checks passed.")
