"""
version.py - firmware version, semantic versioning (MAJOR.MINOR.PATCH).

Bumped by whoever changes the code, not automatically - MAJOR for
breaking changes to the MQTT contract/config schema that would need a
migration, MINOR for new features/non-breaking additions, PATCH for
bug fixes. A plain constants file (matching pins.py's pattern) rather
than part of config.py - like pins.py, this is a fact about which CODE
is running, not a user-editable runtime setting, so it doesn't belong
in the persisted, dashboard-editable config.json.

0.1.0 as the starting point, not 1.0.0: the base's own feature set is
genuinely mature (every driver and /core/ module built and tested, the
full web portal, main.py wiring it all together) but nothing has been
verified on physical hardware yet, and the HACS integration plus the
first BLE module don't exist yet either - see README.md's checklist
for current status. Bump to 1.0.0 once physical hardware verification
and a first real module/HACS round-trip are done.

0.2.0: additive feature set since 0.1.0 - alert light, sensor failure
tracking with dismiss support, LED idle modes, colorblind-safe/custom
color schemes, a display-only C/F toggle. All backward-compatible
(new config fields and MQTT topic fields, nothing renamed or removed
in a way that would break an existing client) - a MINOR bump, not
MAJOR, per the rule above.

Published to HA/MQTT via mqtt_client.py's publish_device_info() as the
device registry's sw_version - see docs/ARCHITECTURE.md. Also used as
the version suffix in the project's distributed zip filename.
"""

VERSION = "0.2.0"
