"""
main.py - Camera Module entry point.

Boot sequence:
1. Load config. If WiFi credentials or an MQTT broker aren't both
   configured yet, start the setup AP and serve the portal - GET "/"
   shows setup.html until both exist (see web_portal.py's routing).
2. Otherwise, attempt to connect. On success, connect base discovery
   and this module's own MQTT/HA presence (mqtt_presence.py) too
   (both need the broker already reachable) and serve the portal -
   GET "/" now shows associate.html, then a small dashboard linking to
   grid layout and settings, once at least one base is associated.
3. On a failed reconnect attempt with previously-saved credentials,
   fall back to AP mode so the user can fix whatever's wrong via the
   same setup form.

Capture scheduling (when a photo actually gets taken, and calling
flash_controller/wilt_watch/drama_level as part of that) is still not
built here - this only wires up config, association, grid/settings
storage, and their MQTT/HA presence, per this task's scope. See
README.md "Remaining".
"""

import config as config_module
import identity
import wifi_manager as wifi_manager_module
from base_association import BaseAssociationManager
from drama_level import DramaLevelManager
from grid_config import GridConfigManager
from mqtt_discovery import BaseDiscovery
from mqtt_presence import CameraMqttPresence
from per_base_settings import PerBaseSettingsManager
from web_portal import serve_forever
from wifi_manager import WifiManager
from wilt_watch import WiltWatchManager


def main():
    cfg = config_module.load()
    camera_id = identity.get_camera_id()

    wifi_mgr = WifiManager(cfg["wifi"]["ssid"], cfg["wifi"]["password"])
    association = BaseAssociationManager(config_module)
    discovery = BaseDiscovery(broker=cfg["mqtt"]["broker"] or None, port=cfg["mqtt"]["port"])

    grid_config = GridConfigManager(config_module)
    per_base_settings = PerBaseSettingsManager(config_module)
    wilt_watch = WiltWatchManager(per_base_settings)
    drama_level = DramaLevelManager()  # not yet called anywhere - see module docstring; kept ready for capture scheduling to use
    presence = CameraMqttPresence(
        config_module, association, per_base_settings, grid_config, camera_id,
        broker=cfg["mqtt"]["broker"] or None, port=cfg["mqtt"]["port"], wilt_watch=wilt_watch,
    )

    ready = wifi_mgr.has_credentials() and bool(cfg["mqtt"]["broker"])
    connected = False

    if ready:
        connected = wifi_mgr.connect_sta()
        if connected:
            discovery.connect()
            presence.connect()
        else:
            print("Could not connect with saved WiFi credentials - falling back to setup AP.")

    if not ready or not connected:
        ssid = wifi_mgr.start_ap(camera_id)
        print("Setup AP started: {}".format(ssid))
        print("Connect to that network, then visit http://{}/ to finish setup.".format(wifi_manager_module.AP_IP))
    else:
        print("Camera Module ready at http://{}/".format(wifi_mgr.ip_address() or "<pending>"))

    serve_forever(wifi_mgr, discovery, association, config_module, grid_config, per_base_settings, wilt_watch, presence)


if __name__ == "__main__":
    main()
