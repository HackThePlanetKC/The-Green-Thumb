"""
main.py - Camera Module entry point.

Scope for this task: WiFi/MQTT-broker setup + base association ONLY -
no grid/settings UI (that's a separate follow-up task, see
docs/ARCHITECTURE.md). A one-shot setup flow, not a long-running
daemon with retry loops - if the saved credentials fail to connect at
boot, this falls back to AP mode once and waits for the user to
resubmit the portal form, rather than automatically retrying with
wifi_manager.next_backoff_s() (kept for a future daemon mode to use,
not wired up here).

Boot sequence:
1. Load config. If WiFi credentials or an MQTT broker aren't both
   configured yet, start the setup AP and serve the portal - GET "/"
   shows setup.html until both exist (see web_portal.py's routing).
2. Otherwise, attempt to connect. On success, connect base discovery
   too (needs the broker to already be reachable) and serve the
   portal - GET "/" now shows associate.html instead.
3. On a failed reconnect attempt with previously-saved credentials,
   fall back to AP mode so the user can fix whatever's wrong via the
   same setup form.
"""

import config as config_module
import identity
import wifi_manager as wifi_manager_module
from base_association import BaseAssociationManager
from mqtt_discovery import BaseDiscovery
from web_portal import serve_forever
from wifi_manager import WifiManager


def main():
    cfg = config_module.load()
    camera_id = identity.get_camera_id()

    wifi_mgr = WifiManager(cfg["wifi"]["ssid"], cfg["wifi"]["password"])
    association = BaseAssociationManager(config_module)
    discovery = BaseDiscovery(broker=cfg["mqtt"]["broker"] or None, port=cfg["mqtt"]["port"])

    ready = wifi_mgr.has_credentials() and bool(cfg["mqtt"]["broker"])
    connected = False

    if ready:
        connected = wifi_mgr.connect_sta()
        if connected:
            discovery.connect()
        else:
            print("Could not connect with saved WiFi credentials - falling back to setup AP.")

    if not ready or not connected:
        ssid = wifi_mgr.start_ap(camera_id)
        print("Setup AP started: {}".format(ssid))
        print("Connect to that network, then visit http://{}/ to finish setup.".format(wifi_manager_module.AP_IP))
    else:
        print("Camera Module ready at http://{}/".format(wifi_mgr.ip_address() or "<pending>"))

    serve_forever(wifi_mgr, discovery, association, cfg, config_module)


if __name__ == "__main__":
    main()
