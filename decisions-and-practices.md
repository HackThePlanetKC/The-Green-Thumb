# Decisions & Practices

A running log of project-level decisions and conventions that don't fit
neatly into [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) (documents
*how the system works*) or [`README.md`](README.md) (documents
*current status*) - things decided along the way that future
contributors should know were deliberate, not accidental or
overlooked. Newest entries at the top.

---

## 2026-09-23 — Base-firmware local UI gap for non-BLE-paired modules: documented, deferred

Captured while building grid/settings, wilt-watch, and drama-level for
the Camera Module (see `docs/ARCHITECTURE.md`'s Camera Module section
and its matching Future Enhancements entry) - per-base settings
(general health/advanced metric toggles) needed three-way editability
(camera portal, that base's own portal, HA), but the base's own local
portal has no way to subscribe to or render MQTT data from a module
it isn't BLE-paired with. Modifying the base firmware to add that
(`core/`, `web/server.py`) was judged out of scope for a camera-module
task - different codebase, its own version bump and testing, not
authorized under that task's scope. Documented instead of built, by
request.

Gap: Base-firmware local UI cannot display settings for non-BLE-paired modules

- The base firmware's local web portal (`web/server.py` / `core/`)
  currently has no mechanism to subscribe to or render MQTT data from
  a module it isn't BLE-paired with (e.g. the Camera Module, which is
  a standalone WiFi/MQTT module, not a BLE peripheral).
- As of the current camera module work, per-base camera settings
  (general health/advanced metric toggles, etc.) are editable via HA
  and the camera module's own portal - full MQTT-layer parity exists
  - but are NOT visible/editable on that base's own local portal. This
  was an explicit scope decision on the camera module task (modifying
  base firmware was out of scope - different codebase, would require
  its own version bump and testing, not authorized under that task).
- Closing this gap means: base firmware gains an MQTT subscription
  client-side for relevant module topics it isn't BLE-paired with,
  plus a new local-portal UI section to render/edit that data. This is
  base-firmware work (`core/`, `web/server.py`), with its own version
  bump and testing - separate from any module's `version.py`.
- This isn't camera-module-specific - any future standalone WiFi/MQTT
  module (not just the camera) would hit the same gap. Worth solving
  generally (a base subscribes to and displays settings for any
  associated module, regardless of pairing method) rather than one-off
  per module.
- Status: deferred, not scheduled. Do not implement.

## 2026-09-23 — Species-based threshold suggestions: documented, deferred

Captured as a future-enhancement idea in `docs/ARCHITECTURE.md`'s new
"Future Enhancements" section, rather than implemented. Note-only, by
request - no code, no version bump. Recorded here specifically so the
reasoning behind *not* building it yet isn't lost between now and
whenever it's picked up.

Two things block implementation, both left open on purpose rather than
guessed at:

- **Data source not chosen.** Candidates so far (Perenual API, Trefle,
  Wikipedia infobox parsing) all have gaps or inconsistent units
  between species entries - real research and evaluation is needed
  before picking one, not a default-to-whichever's-easiest call made
  now just to unblock the idea.
- **Lookup location not decided.** Whether the Camera Module (spare
  compute, its own WiFi/MQTT presence) performs the species lookup and
  relays results through a base's existing MQTT topic namespace, or
  each base performs its own lookup independently, depends on
  constraints (network access, request rate/caching, offline
  behavior) that aren't known yet - deferred to implementation time.

**Practice this reinforces:** keep this feature and the Camera
Module's general-health classifier (not yet built) strictly separate.
The classifier is intended to stay species-agnostic by design - one
generalized health score, no species ID in the image-scoring pipeline
- and this threshold-suggestion feature only ever touches *suggested*
values for a base's other sensors (moisture, light, etc.), never
auto-applied, always requiring explicit user confirmation before a
real threshold changes. Writing this down now, before either exists,
so the boundary doesn't blur once both are being built.
