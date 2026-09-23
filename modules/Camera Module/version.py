"""
version.py - Camera Module firmware version, semantic versioning
(MAJOR.MINOR.PATCH).

Separate from the base station's own /version.py - this module is a
distinct codebase on a distinct platform (Pi/Linux, not MicroPython),
with its own release cadence, so it gets its own semver rather than
being tied to the base's version number. Same MAJOR/MINOR/PATCH
convention as the base for consistency (MAJOR for breaking changes to
this module's MQTT contract/config schema, MINOR for new features,
PATCH for fixes), bumped manually by whoever changes the code.

0.1.0 as the starting point: only the RGBW pre-capture flash subsystem
existed (ring driver, flash trigger logic, config) - capture
scheduling, the light sensor driver it reads from, and MQTT
discovery/publishing didn't exist yet.

0.2.0: added WiFi setup (nmcli-based, mirroring core/wifi.py's API
shape) and MQTT base discovery/association (subscribes to
greenthumb/+/device_info, lets the user pick which base(s) this module
monitors) - both additive, no existing contract changed, so a MINOR
bump. Capture scheduling, grid/settings UI, and the module's own
top-level MQTT/HA presence still don't exist yet - see this module's
README.md for current status.

0.3.0: added grid/region layout (grid_config.py), module-global vs.
per-base settings scoping (per_base_settings.py), the wilt-watch and
drama-level structural comparison metrics (image_compare.py,
wilt_watch.py, drama_level.py), the underlying one-shot capture
primitive they both need (camera_capture.py), and this module's own
top-level MQTT/HA presence for global + per-base settings
(mqtt_presence.py, greenthumb/camera/<camera_id>/global/... and
greenthumb/<base_id>/module/<camera_id>/...). All additive - no
existing topic contract changed, so another MINOR bump. Capture
scheduling and the light sensor driver still don't exist; a base's own
local portal editing its own per-base settings is a documented,
deferred gap (see decisions-and-practices.md) - see this module's
README.md for current status.
"""

VERSION = "0.3.0"
