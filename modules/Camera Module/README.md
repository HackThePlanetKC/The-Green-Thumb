# Camera Module — Visual Health Monitor

A standalone Pi-based module: periodic photos of the plant for visual
health tracking, with a WS2812B RGBW ring for low-light fill flash.
Not a BLE-paired peripheral of a Green Thumb base station (unlike the
base's own `module/<mod_id>/*` MQTT relay contract - see
[`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md)) - this module
runs its own OS, its own MQTT client, and is expected to speak MQTT
discovery directly, independent of any particular base. See that same
doc's `modules/` layout note for why this and future modules live in
their own directories, separate from the base's own `core/`/`drivers/`/
`web/` code.

## Hardware / BOM

| Part | Notes |
|---|---|
| Raspberry Pi Zero 2 W | Runs this module's software (Pi/Linux, not MicroPython) |
| Raspberry Pi Camera Module v3 | Capture hardware - capture scheduling not yet written, see "Remaining" below |
| WS2812B RGBW ring, 7 pixels | Low-light fill flash - **dedicated white channel used for the flash**, not an RGB-mixed approximation (see `ring.py`) |

## Pin assignment

See [`pins.py`](pins.py) — `RING_DATA_PIN = 18` (Pi hardware PWM0), direct 3.3V drive, no level shifter. Full wiring detail: [`BUILD.md`](BUILD.md).

## Driver choice

[`rpi_ws281x`](https://github.com/rpi-ws281x/rpi-ws281x-python) - the reference WS2812 C library's official Python binding - not the CircuitPython/Blinka wrapper (`adafruit-circuitpython-neopixel`). This module runs on plain Raspbian/Linux, not CircuitPython, so the direct binding is the right fit for the platform rather than pulling in Blinka's hardware-abstraction layer for no benefit here. See [`ring.py`](ring.py).

## Configuration

[`config.py`](config.py) — same `DEFAULT_CONFIG` + deep-merge-on-load idiom as the base station's `core/config.py`, kept entirely separate (own `config.json`, own file, no shared state with the base). Currently:

| Key | Default | Notes |
|---|---|---|
| `flash.low_light_threshold` | `50` | Below this reading, a flash fires before capture. Sane starting default, not sourced from a datasheet - tune once mounted and pointed at a real plant/enclosure, same "adjustable, not fixed" spirit as the base station's own thresholds. Units depend on whichever light sensor driver this module ends up using (not written yet). |
| `flash.brightness` | `0.5` | W-channel level (0.0-1.0) while flashing. |

## Flash trigger logic

[`flash_controller.py`](flash_controller.py)'s `maybe_flash(light_level, threshold, exposure_s, ring, brightness, sleep)`: reads the current light level, and if it's below `threshold`, drives the ring's white channel on for `exposure_s` - timed to the camera's exposure for this specific capture, not a fixed constant - then off again. Above threshold, no flash fires. Uses `try`/`finally` around the exposure wait so an exception mid-capture still turns the ring off rather than leaving it lit indefinitely ("do not leave it held on" - see the task this shipped under).

## WiFi setup

[`wifi_manager.py`](wifi_manager.py) — API shape mirrors the base station's own `core/wifi.py` `WifiManager` (`has_credentials()`, `set_credentials()`, `connect_sta()`, `is_connected()`, `ip_address()`, `next_backoff_s()`, `start_ap()`/`stop_ap()`), for cross-project consistency - but the implementation is entirely different: this shells out to `nmcli` (NetworkManager, Raspberry Pi OS's default network stack) rather than driving a MicroPython `network.WLAN()` object, and is fully **synchronous**, not `async`, since nothing else in this module runs an asyncio event loop (deliberate - see that file's module docstring for why forcing `async` here for API-shape parity alone wasn't worth it).

At first boot (or whenever WiFi/MQTT-broker credentials aren't both saved yet), this module starts an open setup hotspot (`GreenThumb-Camera-Setup-<camera_id>`, `camera_id` from [`identity.py`](identity.py) - same 6-hex-char derivation as the base station's `base_id`, but its own separate identifier/namespace) and serves [`static/setup.html`](static/setup.html): scan/select/enter a WiFi network, plus the MQTT broker address/port (bundled into the same step - see [Configuration](#configuration) below for why). Credentials are stored in plain JSON, same as the base station's own approach (see `config.py`) - not a new gap introduced here.

**Different AP-mode gateway IP than the base station**, worth knowing if you're used to the base's `192.168.4.1`: this module's hotspot uses NetworkManager's own default, `10.42.0.1` (`wifi_manager.AP_IP`) - a fact about how `nmcli`'s hotspot mode behaves, not a project-wide convention.

## MQTT discovery and base association

[`mqtt_discovery.py`](mqtt_discovery.py) — once online, subscribes to `greenthumb/+/device_info` (the base station's retained "who am I" topic - see `core/mqtt_client.py`'s `publish_device_info()` in the base firmware repo) and builds a live list of known bases as they're seen, keyed by `base_id`. Uses [`paho-mqtt`](https://pypi.org/project/paho-mqtt/) (the standard Pi/Linux MQTT client) with `CallbackAPIVersion.VERSION2` pinned explicitly - not a hand-rolled client like the base's `umqtt.simple` usage, which exists there specifically because MicroPython has no equivalent built in; no such constraint applies here.

[`base_association.py`](base_association.py) — stores which base(s) this module is associated with, as a list of `base_id` strings (**never `friendly_name`**, which is a display-only, user-editable label that can change at any time - see `core/mqtt_client.py`'s `friendly_name` field, which exists specifically so this module has something human-readable to show in the picker instead of a raw `base_id`).

Once WiFi + broker are both configured, [`static/associate.html`](static/associate.html) lists every discovered base by `friendly_name` with a checkbox per base - a base only appears once it's been seen online at least once (its retained `device_info` is what this list is built from). A base may be associated with multiple cells in the grid layout below (expected to be contiguous, though that's not enforced - see Grid Layout).

## Grid layout

[`grid_config.py`](grid_config.py) — divides the camera's frame into up to 4x4 regions (rows and columns are independent bounds, so non-square layouts like 2x4 or 1x3 are fully supported, not just square presets) and maps each cell to one of this module's associated bases via [`static/grid.html`](static/grid.html): capture a reference still, then assign each overlaid cell to a base (or leave it unassigned) from a dropdown. A base may be assigned multiple cells - expected to be contiguous, but **contiguity is not validated**; the click-to-assign UI makes an accidental non-contiguous assignment unlikely, and real flood-fill/graph-connectivity validation for a constraint that's already hard to violate by accident wasn't judged worth the added complexity (documented as a known simplification, not an oversight).

`cell_pixel_bbox(base_id, image_width, image_height)` maps a base's assigned cell(s) to a pixel bounding box against a given photo's dimensions - used by the wilt-watch reference-capture flow (below) to know which crop belongs to which base.

`set_grid(rows, cols, cells)` applies a full grid change (dimensions + the complete cell map) in one atomic load/validate/save - every cell is checked against the associated-base list before anything is written, so an invalid request changes nothing at all rather than partially applying. Both the web portal's `POST /save_grid` and the global MQTT `set_grid` command (see below) call this one method, rather than each looping over individual `set_grid_dimensions()`/`set_cell_assignment()` calls (still available individually, and what `set_grid()` itself is built from, but calling them one cell at a time from a request handler was a real bug caught by review - a validation failure partway through the loop could leave earlier cells' writes already persisted).

Grid config is **module-global**: stored in this module's own config, editable only via the camera portal or HA - never from an individual base's own portal.

## Settings scoping

[`per_base_settings.py`](per_base_settings.py) splits settings into two tiers:

- **Module-global** (camera portal + HA only, never a base's own portal): capture frequency (`capture.frequency_per_day`, default 2x/day), grid layout/assignment (above), and flash on/off + low-light threshold (`flash.enabled`, `flash.low_light_threshold`).
- **Per-base** (camera portal + HA - see [`static/settings.html`](static/settings.html)), keyed by `base_id`: general health (always on - not a stored/toggleable setting, since there's no value in persisting something that can never be anything but `True`) plus eight independent opt-ins: `chlorosis`, `necrosis`, `spotting`, `leaf_scorch`, `powdery_mildew`, `pest_indicators` (the six heuristic visual detectors, see below), `wilt_watch`, `drama_level`. Enabling one never implies another.

**A given base's own local portal editing only its own settings (full three-way parity: camera portal, that base's own portal, HA) is explicitly NOT built here.** The base firmware's local portal has no mechanism to subscribe to or render MQTT data from a module it isn't BLE-paired with - closing that gap is base-firmware work (`core/`, `web/server.py`) with its own scope/version bump, not authorized under this task. See `decisions-and-practices.md` and `docs/ARCHITECTURE.md`'s matching Future Enhancements entry.

Detector thresholds/sensitivity (below) are **module-global**, not per-base, even though each detector's on/off toggle above is per-base - see `decisions-and-practices.md` for why (the project's only existing threshold pattern is module/device-global, so that's what "consistent with existing patterns" pointed to).

## Heuristic visual detectors

Six single-frame, absolute (no reference/previous-capture comparison - that's wilt-watch/drama-level's job, below) color/texture detectors, all species-agnostic, heuristic/rule-based OpenCV analysis - **not** an ML classifier and **not** trained on an external dataset. Each is one of the per-base opt-ins above. Every result dict includes at least `detected` (bool) and `confidence` (0.0-1.0); most also include an `affected_area_pct` (or `affected_margin_pct`/`lesion_count` where that fits better).

| File | Detector | How | Output |
|---|---|---|---|
| [`chlorosis.py`](chlorosis.py) | Chlorosis (yellowing) | HSV; flags pixels shifted toward yellow relative to THIS photo's own healthy-green reference population (mean/std-dev of hue), not a fixed absolute yellow window | `affected_area_pct` |
| [`necrosis.py`](necrosis.py) | Necrosis (dead/dying tissue) | Low-saturation dark/brown/grey/black patch detection ([`discoloration.py`](discoloration.py)'s shared primitive), measured uniformly across the whole leaf | `affected_area_pct` |
| [`spotting.py`](spotting.py) | Spots/lesions | LAB color-outlier connected-component analysis, tuned for a FEW LARGER lesions (higher min area, no count requirement) | `lesion_count` + `affected_area_pct` |
| [`leaf_scorch.py`](leaf_scorch.py) | Leaf scorch | Reuses necrosis's exact color primitive, weighted by distance-from-margin (`cv2.distanceTransform`, normalized per-leaf) instead of measured uniformly - the one thing distinguishing it from necrosis | `affected_margin_pct` |
| [`powdery_mildew.py`](powdery_mildew.py) | Powdery mildew | Requires BOTH a light/white color signal AND a texture signal (per-pixel Laplacian magnitude) together - neither alone is enough | `affected_area_pct` |
| [`pest_indicators.py`](pest_indicators.py) | Pest indicators (one grouped toggle, three sub-checks) | **webbing**: Canny edge density. **pest_clusters**: spotting's own LAB color-outlier primitive, tuned the OPPOSITE way (many small components, a minimum count). **stippling**: many small discrete light components, color-only, no texture check | `triggered` (list of which sub-check(s) fired) + per-sub-check detail |

**Distinguishing powdery mildew from pest_indicators' stippling** (both are "light speckling" and can visually overlap - documented explicitly per this task's own requirement): mildew requires texture (a Laplacian signal) on top of color and treats the result as one or a few fairly contiguous coated regions; stippling uses color only and instead distinguishes itself by connected-component size - many small, separate, discrete dots rather than a contiguous area. See [`pest_indicators.py`](pest_indicators.py)'s own module docstring and [`tests/test_pest_indicators.py`](tests/test_pest_indicators.py), which demonstrates both directions (a dot pattern scores low on mildew; a genuine coating doesn't register as stippling).

**Pest indicators' confidence is deliberately capped below the other detectors' 1.0 ceiling** (all three sub-checks) - dust, fibers, and ordinary leaf texture can trigger signals structurally similar to webbing/stippling, so even a strong raw signal there is reported as less certain than an equally strong chlorosis/necrosis/etc. reading. A documented, conservative choice, not a claim that stronger evidence doesn't exist.

Shared building blocks ([`detector_common.py`](detector_common.py)): `compute_leaf_mask()` (a broad "is this plant tissue, not background" HSV heuristic - deliberately not green-only, since a chlorotic/necrotic/mildew-coated region must still count as leaf; two documented known limitations - non-white/black background clutter can be misclassified as leaf, and the near-white/near-black background exclusion can itself exclude a severe mildew coating or truly black necrotic tissue, see that function's own docstring), `color_outlier_mask()` (the shared LAB primitive spotting/pest_clusters both use), and `confidence_from_ratio()` (shared 0.0-1.0 confidence curve). **Every detector result value is explicitly cast to a native Python type** - a real bug caught by this module's own tests: numpy arithmetic can produce `np.bool_`/`np.float64` values that aren't identical to Python's `True`/float by identity and aren't JSON-serializable (which matters once results are published over MQTT); see `detector_common.py`'s module docstring.

### Disclaimer (two-tier)

[`visual_disclaimers.py`](visual_disclaimers.py) defines both tiers once, referenced everywhere - never duplicated inline:

- `FULL_DISCLAIMER` - shown on settings pages where these detectors are configured: this module's own `/settings` page, and published as a `detector_disclaimer` field on each base's MQTT state topic (`greenthumb/<base_id>/module/<camera_id>/state`) for HA's settings view. Not shown on a base's own local portal, since that portal doesn't exist for this data (see Settings scoping above).
- `SHORT_DISCLAIMER` ("Heuristic detection, not a diagnosis.") - for wherever a detection *result* is eventually surfaced (dashboard, notification, HA entity state). No such surface exists yet - capture scheduling and result publishing aren't built (see Status below) - but the constant is defined now so that code has one source to reference instead of inventing its own wording later.

Detectors require OpenCV + numpy (`pip install opencv-python-headless numpy` on the real Pi) - a new dependency for this module, on top of `rpi_ws281x`, `paho-mqtt`, and Pillow. Import-guarded the same way (`detector_common.py` raises a clear `RuntimeError` if called without them installed), but unlike those other guarded dependencies, this module's own tests for the six detectors DO require the real libraries installed - see Tests below.

## Wilt-watch and drama-level

[`image_compare.py`](image_compare.py) provides the one comparison primitive both metrics share: `structural_difference()`, a per-column "first dark pixel from the top" silhouette-edge profile compared column-by-column between two same-width `GrayscaleImage`s - **structural (droop/turgor, position), not color-based** (there's no color channel in a `GrayscaleImage` at all), and deliberately kept separate from the not-yet-built, species-agnostic health classifier (a color/appearance question, not a shape one - see `docs/ARCHITECTURE.md`'s species-threshold Future Enhancement for why that separation matters). `_EDGE_THRESHOLD` is a documented starting-point constant, not validated against real plants yet - same "tune once pointed at a real plant, not sourced from a datasheet" spirit as this module's light/flash thresholds. `GrayscaleImage` is this module's own dependency-free representation (width/height/flat pixel list) - `load_grayscale_from_file()` is the only function that needs Pillow (import-guarded, only required on the real Pi), so every comparison, including every test, works on plain synthetic data with no image library installed at all.

[`wilt_watch.py`](wilt_watch.py) — compares a base's region crop against a **stored reference** image. Turning wilt-watch on for a base with no reference yet sets `wilt_watch_config_necessary` (surfaced in the portal and that base's own MQTT/HA state) - no wilt level is computed until a reference exists (`has_reference()`/`compute_wilt_level()` returning `None`). The reference is captured via [`static/settings.html`](static/settings.html)'s prompt, stored locally on this module's filesystem (`wilt_reference_<base_id>.json`, plain JSON like the rest of this module's storage), and **never published over MQTT** unless the separately-planned image-passthrough option (not built) is also enabled.

[`drama_level.py`](drama_level.py) — compares a base's region crop against the **immediately previous** capture of the same region, no reference or config-necessary step needed. Represents rate of change between consecutive captures, not absolute severity; overwrites its stored "previous" image (`drama_previous_<base_id>.json`) every call. Not yet wired into any live capture loop (capture scheduling isn't built - see Status below), but ready for one to call.

[`camera_capture.py`](camera_capture.py) — the one piece of real camera I/O either metric needs: a minimal, injectable `capture_still()` wrapping `rpicam-still` for "take one photo right now." Deliberately NOT capture scheduling (still not built) - just the primitive both the grid setup page and wilt-watch's reference capture need today.

## Image library (local storage, retention, portal library, HA thumbnail passthrough)

[`image_library.py`](image_library.py) — real (full-color) JPEG storage per zone (a zone = an associated `base_id`, same keying as `per_base_settings.py`), **deliberately independent of `wilt_watch.py`/`drama_level.py`'s own storage** - both those modules persist their own grayscale, cropped images for their own structural-comparison math, and this module never reads or writes those files, nor do they read or write this module's. All three read from the same original capture at the moment it's taken, then each persists its own representation for its own consumer - a documented decision, not an oversight (see `decisions-and-practices.md`).

Per zone, on disk under `data/library/<zone>/`:

- Three rotating **working slots**: `current.jpg` (latest capture), `most_recent.jpg` (the prior capture - `record_capture()` rotates current → most_recent, carrying its metadata along, before overwriting current with the new capture), `reference.jpg` (a copy made whenever wilt-watch's own reference is (re)captured via `static/settings.html` - `record_reference()` just overwrites it, no rotation). A zone's very first capture has nothing to rotate into most_recent yet; wilt-watch must be enabled and a reference captured at least once before `reference.jpg` exists.
- An independent, capped **saved/pinned set** (`saved/<image_id>.jpg`, 12-hex-char ids) - pinning (`save_image()`) copies a working slot's current bytes into its own permanent file, so a later capture rotating "current" never affects an image the user chose to keep (item 2). Each zone's own `max_saved_images` setting (see below) is enforced by **blocking** a pin once reached (`ValueError`, changing nothing) - **never** silently evicting an existing pinned image (item 3).
- One `index.json` per zone: `captured_at`/`pinned_at`/`source` metadata only, **never image bytes**, written atomically (temp file + `os.replace()`, same pattern `config.py` uses). File existence is always double-checked against the index on read (`list_zone()`), so a manually-deleted file behind the index's back is never reported as still present.

No image data - full-resolution or thumbnail - is ever published over MQTT by `image_library.py` itself; see the thumbnail passthrough section below for the one opt-in exception, which lives in `mqtt_presence.py`, not here.

**Capturing into the library** is manual - a "Capture Now" button on each zone's library page (below), mirroring the existing grid-reference/wilt-watch-reference capture buttons, since no capture scheduler exists yet (see Status). It captures a fresh still, crops it to the zone's assigned grid cell(s) (`grid_config.cell_pixel_bbox()`), and rotates the crop into Current/Most Recent via `record_capture()`. Capturing a wilt-watch reference (`static/settings.html`) additionally calls `record_reference()`, populating the library's Reference slot from the same capture - one photo, two independent consumers, per the module docstring above.

### Portal pages

- [`static/library.html`](static/library.html) (`GET /library`) - overview across every associated zone: a card per zone with its friendly name and saved-image count, linking to that zone's own page (item 6).
- [`static/zone_images.html`](static/zone_images.html) (`GET /library/<zone>`) - one zone's Current/Most Recent/Reference/pinned images (item 7), each with an inline preview and a download link/button, a "Capture Now" button, a "pin" action per working-slot image (item 9), an "unpin" action per saved image, and this zone's own library settings form (`max_saved_images`, thumbnail passthrough - see below). Also linked directly from that base's own settings page (`static/settings.html`), consistent with how per-base settings are already surfaced there (item 8).
- `GET /library/<zone>/download/<kind>[/<image_id>]` (`kind` is `current`/`most_recent`/`reference`, or `saved` with a required `image_id`) serves the raw JPEG bytes with `Content-Type: image/jpeg` and **deliberately no `Content-Disposition` header** - the same URL is both the `<img>` preview source and the download link; some browsers honor `Content-Disposition: attachment` even for an `<img>` tag's subresource fetch, which would silently break inline previews. The "Download" affordance is instead the HTML5 `download` attribute on `zone_images.html`'s `<a>` tags, which forces a save client-side with no server-side header needed. `zone` is validated against this module's own associated-base list and `image_id` against a strict 1-32 char hex pattern (matching the `uuid4().hex[:12]` format `image_library.py` generates) **before either is ever used in a filesystem path** - closing a path-traversal risk that would otherwise exist via `os.path.join()` with an attacker-controlled path segment.

### Settings (three-way parity: camera portal, item 5/10)

Two more per-zone settings live in `per_base_settings.py`'s existing per-`base_id` entry, alongside the eight METRICS toggles, but are **not** boolean METRICS (they have their own setters, `set_max_saved_images()`/`set_thumbnail_passthrough_enabled()`, not `set_metric()`):

| Setting | Default | Notes |
|---|---|---|
| `max_saved_images` | `10` | Per-zone cap on pinned/saved images, 1-100. A sane starting default (a small handful of "keepers"), not sourced from a storage-capacity calculation - tune per-install once real image sizes/SD card capacity are known. |
| `thumbnail_passthrough_enabled` | `False` | Per-zone opt-in: pass a downsampled thumbnail of Current to HA over MQTT on every capture (item 10) - see below. Off by default, since it's optional data passthrough, not a required part of the module's core function. |

Both are edited from `zone_images.html`'s settings form (`POST /save_library_settings`, `base_id` + `max_saved_images` + `thumbnail_enabled`), which republishes that base's MQTT state on success, same as `_handle_save_per_base_metric`. **Full three-way parity (camera portal, that base's own portal, HA) is explicitly not built for either setting**, for the same reason it isn't built for the other per-base settings - see [Settings scoping](#settings-scoping) above and `decisions-and-practices.md`.

### Thumbnail passthrough to HA (item 10) and full-resolution download (item 11)

If a zone has `thumbnail_passthrough_enabled` on, every "Capture Now" downsamples the new Current image (`image_library.thumbnail_bytes()`, longest side capped at `config.py`'s module-global `image_library.thumbnail_max_dimension`, default `320` - sane small default, not hardcoded into the downsample call itself) and publishes the raw JPEG bytes (retained) to `greenthumb/<base_id>/module/<camera_id>/thumbnail` via [`mqtt_presence.py`](mqtt_presence.py)'s `publish_thumbnail()`. **Raw bytes, not JSON/base64** - deliberately matching Home Assistant's own MQTT Camera entity convention (an image topic's payload is the image itself). A zone with the setting off never has anything published to this topic.

**For a full-resolution image, HA (or any other MQTT-side consumer) calls this module's own local HTTP endpoint directly** - the thumbnail topic is the only image data ever pushed over MQTT; nothing this size is ever inlined into a retained message. `GET http://<camera module's IP>:<web portal port>/library/<zone>/download/current` (or `most_recent`/`reference`/`saved/<image_id>`) returns the real JPEG, `Content-Type: image/jpeg`, no auth beyond being reachable on the local network (same trust boundary as every other route this module's portal exposes). This is the endpoint to wire into an HA automation/script (e.g. a `rest` or `camera` config pointing at it) for on-demand full-resolution retrieval - documented here specifically for that integration purpose (item 11).

## This module's own MQTT/HA presence

[`mqtt_presence.py`](mqtt_presence.py) publishes two deliberately isolated topic trees, over its own separate `paho-mqtt` connection (distinct from `mqtt_discovery.py`'s own - two connections from one process is a real but minor cost, simpler than mixing "learn what bases exist" and "publish/handle this module's own settings" into one class):

- `greenthumb/camera/<camera_id>/global/{status,device_info,config,command}` - module-global settings ONLY. One minimal HA device card for the camera module itself; per-base health/metric data never appears here.
- `greenthumb/<base_id>/module/<camera_id>/{status,state,command}` - per-base settings, reusing the base station's own documented `module/<mod_id>/*` topic shape so each base's settings attach to that base's own existing HA device, published directly by this module (not relayed by the base, since this module isn't BLE-paired - see `docs/ARCHITECTURE.md`). `.../thumbnail` (same tree) is the one exception to "settings data only" here - see [Image library](#image-library-local-storage-retention-portal-library-ha-thumbnail-passthrough)'s thumbnail passthrough section for why an image topic lives alongside the settings topics rather than under a separate tree.

The global `command` topic accepts a generic `set_config` ({"action": "set_config", "path": ..., "value": ...}, mirroring `core/mqtt_client.py`'s own handler shape) restricted to a whitelist of top-level keys (`capture`, `flash`) - it can never touch this module's own WiFi/broker credentials or association list. `grid` is deliberately excluded from that whitelist: dimensions/cell assignments need real validation (bounds, associated-base checks) a raw dotted-path setter can't provide, so grid edits instead use a dedicated `set_grid` action ({"action": "set_grid", "rows": ..., "cols": ..., "cells": ...}), routed through [`grid_config.py`](grid_config.py)'s `set_grid()` - the same atomic, validated method the web portal's `/save_grid` route uses, so both write paths share one implementation instead of each reimplementing (and potentially diverging on) validation. Per-base `command` topics accept a narrower `set_metric` ({"action": "set_metric", "metric": ..., "enabled": ...}) instead.

## Web portal

[`web_portal.py`](web_portal.py) — a minimal HTTP server for every page above, deliberately built on Python's standard-library `http.server` rather than a hand-rolled async server like the base station's own `web/server.py`. That approach exists there because MicroPython's whole `main.py` runs one asyncio event loop and a vendored framework's overhead isn't worth it on an ESP32-C3's limited RAM - neither constraint applies on a Pi, and the standard library already ships a working HTTP server, so reimplementing HTTP parsing a second time here would be duplicated effort with no benefit. See that file's module docstring.

`GET /` is context-sensitive: shows the setup page until WiFi + broker are both configured, then the association page until at least one base is associated, then a small dashboard linking to Grid Layout and Settings (the latter two are ongoing-editable pages, not one-shot setup steps, so `/` stops advancing through them automatically once at least one base is associated).

No long-lived shared config dict is held in this file - every handler does its own `config_module.load()`/`save()` immediately around the read/mutation it needs, same pattern `grid_config.py`/`per_base_settings.py`/`base_association.py` already use. An earlier version held one config dict loaded once at startup and mutated in place for WiFi/MQTT settings only - a real staleness hazard once multiple independent managers read/write the same `config.json`, since a save from a long-held stale dict could silently clobber a fresh load/save cycle's writes. Fixed by standardizing on one load-mutate-save pattern everywhere.

## Configuration

[`config.py`](config.py) — same `DEFAULT_CONFIG` + deep-merge-on-load idiom as the base station's `core/config.py`, kept entirely separate (own `config.json`, own file, no shared state with the base). Currently:

| Key | Default | Notes |
|---|---|---|
| `flash.low_light_threshold` | `50` | Below this reading, a flash fires before capture. Sane starting default, not sourced from a datasheet - tune once mounted and pointed at a real plant/enclosure, same "adjustable, not fixed" spirit as the base station's own thresholds. Units depend on whichever light sensor driver this module ends up using (not written yet). |
| `flash.brightness` | `0.5` | W-channel level (0.0-1.0) while flashing. |
| `flash.enabled` | `true` | Module-global on/off for the whole flash subsystem - not yet gating any real call site (capture scheduling, which would check this, isn't built), but present in config/MQTT/HA from the start. |
| `wifi.ssid` / `wifi.password` | `""` / `""` | Set via the setup portal. Plain JSON, matching the base station's own (non-encrypted) approach. |
| `mqtt.broker` / `mqtt.port` | `""` / `1883` | User-entered, no auto-discovery mechanism (none exists anywhere in this project - the base station's own settings page requires manual broker entry too). Bundled into the WiFi setup step specifically because base discovery - this module's very next setup step - needs a broker connection to work at all. |
| `associated_base_ids` | `[]` | List of `base_id` strings this module is associated with - see [MQTT discovery and base association](#mqtt-discovery-and-base-association) above. |
| `capture.frequency_per_day` | `2` | Module-global. Capture scheduling itself isn't built yet - this is the setting a future scheduler will read. |
| `grid.rows` / `grid.cols` | `1` / `1` | Module-global grid dimensions, 1-4 each independently - see [Grid layout](#grid-layout). |
| `grid.cells` | `{}` | Module-global. `"row,col"` (0-indexed) -> `base_id`; a cell absent from this dict is unassigned. |
| `per_base_settings` | `{}` | Keyed by `base_id` - see [Settings scoping](#settings-scoping). |
| `detectors.*` | see `config.py` | Module-global thresholds/sensitivity for the six heuristic visual detectors, one sub-dict per detector (`detectors.pest_indicators` further nests `webbing`/`pest_clusters`/`stippling`). Sane starting defaults, not sourced from a dataset - see [Heuristic visual detectors](#heuristic-visual-detectors) above and `decisions-and-practices.md` for why these are module-global rather than per-base. |
| `image_library.thumbnail_max_dimension` | `320` | Module-global longest-side pixel dimension for the downsampled thumbnail optionally passed through to HA over MQTT - see [Image library](#image-library-local-storage-retention-portal-library-ha-thumbnail-passthrough) above. A threshold, so module-global by the same convention as `detectors.*`, even though the opt-in itself (`thumbnail_passthrough_enabled`) is per-zone. |
| `per_base_settings.<base_id>.max_saved_images` | `10` | Per-zone cap on pinned/saved library images, 1-100 - see Image library above. |
| `per_base_settings.<base_id>.thumbnail_passthrough_enabled` | `false` | Per-zone opt-in for the MQTT thumbnail passthrough - see Image library above. |

## Status

**WiFi setup, MQTT discovery/base association, grid layout, settings scoping, wilt-watch, drama-level, the six heuristic visual detectors, the local image library (storage/retention, portal library pages, HA thumbnail passthrough), and this module's own top-level MQTT/HA presence are all built.** The flash subsystem (ring driver, trigger logic) was built first. Not yet built:

- Capture scheduling (when a photo actually gets taken, and calling `flash_controller`/`wilt_watch`/`drama_level`/the six detectors as part of that) - `camera_capture.capture_still()` (the underlying "take one photo now" primitive) exists and is used by the grid setup page and wilt-watch's manual reference capture, but nothing calls it on a schedule yet, and no detector is wired into a live capture loop or a results-publishing/dashboard surface (`SHORT_DISCLAIMER` is defined and tested but has no display call site yet)
- The light sensor driver `flash_controller.maybe_flash()` reads from (its reading is passed in, not sourced here yet)
- A given base's own local portal showing/editing its own per-base settings (full three-way parity) - the base firmware has no mechanism to display MQTT data from a non-BLE-paired module; documented as a gap, not built (see `decisions-and-practices.md` and `docs/ARCHITECTURE.md`)
- Ring mechanical mounting (see [`BUILD.md`](BUILD.md) for the glare/reflection constraint that governs where it can go)

## Tests

Stub/mock tests, no real Pi hardware, `rpi_ws281x`, `nmcli`/NetworkManager, or MQTT broker required - run any of them directly with `python3 tests/<name>.py` from this directory. **Exception:** the six detector test files (and `test_detector_common.py`) require numpy + opencv-python-headless actually installed (`pip install opencv-python-headless numpy`) - unlike every other guarded dependency in this module (Pillow, `rpi_ws281x`, `nmcli`), these tests exercise the real OpenCV algorithm itself, not a stubbed-out decision layer around it, so there's no meaningful way to test them without the real library. See `detector_common.py`'s module docstring.

| File | Covers |
|---|---|
| [`tests/test_ring.py`](tests/test_ring.py) | Ring driver - every `set_white()` call is W-channel-only, R/G/B always 0 |
| [`tests/test_flash_controller.py`](tests/test_flash_controller.py) | Flash trigger decision logic, exception-safety |
| [`tests/test_identity.py`](tests/test_identity.py) | `camera_id` derivation from a fake sysfs MAC address |
| [`tests/test_wifi_manager.py`](tests/test_wifi_manager.py) | WiFi scan/connect/status parsing against a fake `nmcli` |
| [`tests/test_mqtt_discovery.py`](tests/test_mqtt_discovery.py) | Retained-message parsing, malformed input handling, connect/disconnect against a fake MQTT client |
| [`tests/test_base_association.py`](tests/test_base_association.py) | Association persistence round trip |
| [`tests/test_grid_config.py`](tests/test_grid_config.py) | Grid dimensions (incl. non-square), cell assignment/validation, `cell_pixel_bbox()` |
| [`tests/test_per_base_settings.py`](tests/test_per_base_settings.py) | Per-base metric toggles (all eight), general_health non-toggleability, `wilt_watch_config_necessary` flag lifecycle, `max_saved_images`/`thumbnail_passthrough_enabled` setters (valid + invalid, cross-base isolation), and a pre-existing ("legacy") per-base entry missing the new keys still getting sane defaults |
| [`tests/test_image_library.py`](tests/test_image_library.py) | Working-slot rotation (current/most_recent/reference), pin/unpin with cap enforcement (blocks rather than evicts), per-zone isolation, `thumbnail_bytes()` downsampling, `crop_and_save()`, and `image_path()`/`kind` validation. Requires Pillow installed. |
| [`tests/test_image_compare.py`](tests/test_image_compare.py) | `GrayscaleImage` crop/round-trip, `structural_difference()` against synthetic silhouette data |
| [`tests/test_wilt_watch.py`](tests/test_wilt_watch.py) | Reference-capture flow, `None` wilt level before a reference exists, per-base isolation |
| [`tests/test_drama_level.py`](tests/test_drama_level.py) | Rolling previous-capture comparison, `None` on first capture, per-base isolation |
| [`tests/test_mqtt_presence.py`](tests/test_mqtt_presence.py) | Global vs. per-base topic isolation, `set_config`/`set_metric` command handling incl. whitelist rejection, base sync/unsync, `detector_disclaimer` field on per-base state, `publish_thumbnail()` (correct topic, raw bytes not JSON, retained, isolation from other topics, safe no-op when disconnected) |
| [`tests/test_web_portal.py`](tests/test_web_portal.py) | HTTP routes end-to-end against a real loopback server (fake collaborators) - status codes, routing, HTML-escaping of user-controlled data, grid/settings/wilt-watch flows, the six per-base detector toggles, the full disclaimer on `/settings`, and the image library's routes (library/zone pages, capture, download incl. binary JPEG content-type and no-Content-Disposition, pin/unpin incl. cap enforcement without eviction, path-traversal rejection for zone/image_id, `/save_library_settings`, and thumbnail-passthrough opt-in/opt-out on capture) |
| [`tests/test_visual_disclaimers.py`](tests/test_visual_disclaimers.py) | `FULL_DISCLAIMER`/`SHORT_DISCLAIMER` content sanity checks (no cv2/numpy required) |
| [`tests/test_detector_common.py`](tests/test_detector_common.py) | `compute_leaf_mask()`, `confidence_from_ratio()`, `color_outlier_mask()` |
| [`tests/test_config_detectors.py`](tests/test_config_detectors.py) | `detectors` config deep-merge, incl. 3-level-deep `pest_indicators` sub-dicts (no cv2/numpy required) |
| [`tests/test_chlorosis.py`](tests/test_chlorosis.py) | Adaptive yellow-vs-green-variance detection against synthetic images, including the "no green reference in frame" edge case |
| [`tests/test_necrosis.py`](tests/test_necrosis.py) | Uniform (non-margin-weighted) dark-patch detection; documents the near-black-vs-shadow leaf-mask edge case |
| [`tests/test_spotting.py`](tests/test_spotting.py) | Few-larger-lesion connected-component tuning; tiny noise filtered out |
| [`tests/test_leaf_scorch.py`](tests/test_leaf_scorch.py) | The margin-weighting behavior that distinguishes it from necrosis - same discoloration, margin vs. interior placement |
| [`tests/test_powdery_mildew.py`](tests/test_powdery_mildew.py) | Color-AND-texture-required behavior (color-only and texture-only each fail alone); documents the mildew-vs-near-white-background-exclusion edge case |
| [`tests/test_pest_indicators.py`](tests/test_pest_indicators.py) | All three sub-checks (webbing/pest_clusters/stippling), confidence capping, spotting's opposite tuning, and the cross-file mildew-vs-stippling distinguishing behavior |
| [`tests/synthetic_images.py`](tests/synthetic_images.py) | Not a test file itself - shared synthetic-BGR-image helpers the detector test files above import |
