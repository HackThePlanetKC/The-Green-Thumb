"""
base_association.py - Which discovered bases this camera module is
associated with.

Stores associations as a list of base_id strings - never
friendly_name, which is a display-only, user-editable label (see
core/mqtt_client.py's publish_device_info() in the base firmware repo)
that can change at any time and would silently break the association
if used as the stored key.

A thin wrapper around config.py rather than its own storage file - one
config.json for this whole module (flash settings, WiFi/MQTT
credentials, and this), not a second persisted file to keep in sync.
"""


class BaseAssociationManager:
    def __init__(self, config_module):
        """
        config_module: the config.py module itself (or anything
        exposing load()/save() with the same signature) - injected so
        tests point this at a throwaway config file instead of this
        module's real config.json. See tests/test_base_association.py.
        """
        self._config_module = config_module

    def associated_base_ids(self):
        cfg = self._config_module.load()
        return list(cfg.get("associated_base_ids", []))

    def set_associated_base_ids(self, base_ids):
        """
        Replaces the full association list - the portal/HA always
        submits the complete desired set (every currently-checked box),
        not an incremental add/remove, so this mirrors that shape
        rather than exposing separate add_one()/remove_one() methods
        that would need their own dedup/ordering logic on top.
        """
        cfg = self._config_module.load()
        cfg["associated_base_ids"] = list(dict.fromkeys(base_ids))  # de-dup, preserve order
        self._config_module.save(cfg)
