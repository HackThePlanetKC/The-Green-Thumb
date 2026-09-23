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

0.4.0: added six heuristic, rule-based OpenCV visual detectors
(chlorosis.py, necrosis.py, spotting.py, leaf_scorch.py,
powdery_mildew.py, pest_indicators.py, plus detector_common.py and
discoloration.py's shared primitives) as new per-base opt-in settings
(per_base_settings.py's METRICS extended from five to eight), their
module-global thresholds (config.py's new "detectors" key), and the
two-tier disclaimer (visual_disclaimers.py) surfaced on the settings
page and each base's MQTT state. A new dependency for this module:
opencv-python-headless + numpy (see BUILD.md). All additive - no
existing topic/config contract changed for anything that already
existed, so another MINOR bump. Capture scheduling still doesn't
exist, so none of these detectors are wired into a live capture loop
yet - see this module's README.md for current status.

0.5.0: added the local image library (image_library.py) - per-zone
(associated base_id) real JPEG storage/retention with three rotating
working slots (current/most_recent/reference) and an independent,
user-pinned, per-zone-capped saved set; portal library pages
(static/library.html, static/zone_images.html - overview + per-zone
Current/Most Recent/Reference/pinned images with download and pin/
unpin actions); an opt-in downsampled-thumbnail passthrough to HA over
MQTT (greenthumb/<base_id>/module/<camera_id>/thumbnail, raw JPEG
bytes) with the resolution set by config.py's new module-global
image_library.thumbnail_max_dimension; and a local HTTP download
endpoint (GET /library/<zone>/download/<kind>[/<image_id>]) for HA or
any local consumer to fetch a full-resolution image on demand.
Deliberately independent storage from wilt_watch.py/drama_level.py,
which keep their own separate grayscale comparison images unchanged -
see decisions-and-practices.md. Two new per-base settings
(per_base_settings.py: max_saved_images, thumbnail_passthrough_enabled)
with their own setters, not part of the existing METRICS toggle set.
All additive - no existing topic/config contract changed for anything
that already existed, so another MINOR bump. Capture into the library
is manual (a Capture Now button, same as the existing grid/wilt-watch
reference captures) since capture scheduling still doesn't exist - see
this module's README.md for current status.
"""

VERSION = "0.5.0"
