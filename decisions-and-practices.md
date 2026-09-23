# Decisions & Practices

A running log of project-level decisions and conventions that don't fit
neatly into [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) (documents
*how the system works*) or [`README.md`](README.md) (documents
*current status*) - things decided along the way that future
contributors should know were deliberate, not accidental or
overlooked. Newest entries at the top.

---

## 2026-09-23 — Heuristic visual detector thresholds: module-global, not per-base

Six heuristic visual detectors (chlorosis, necrosis, spotting, leaf
scorch, powdery mildew, pest indicators - see
`modules/Camera Module/README.md`) were added with two kinds of
settings: an opt-in per-base toggle (does this base run this
detector at all) and a per-detector sensitivity/threshold (how
aggressively it flags something). The task asked for thresholds
"consistent with existing threshold config patterns in the project" -
every existing threshold in this codebase (the base station's own
sensor thresholds, this module's `flash.low_light_threshold`) is
module/device-global, not per-base. No per-base threshold pattern
exists anywhere to be consistent WITH.

Decision: thresholds live in `config.py`'s `DEFAULT_CONFIG["detectors"]`
- module-global, one shared set of thresholds for every base this
module monitors - while each detector's enabled/disabled toggle stays
per-base (`per_base_settings.py`), since which checks a base wants
running is inherently a per-base choice in a way a CV tuning constant
isn't (in v1, at least - nothing rules out per-base threshold overrides
later if different plants/species turn out to need genuinely different
tuning, but that's not assumed here without evidence).

**Practice this reinforces:** when an instruction says "consistent with
existing patterns" and the codebase only has one shape of that pattern,
follow the existing shape rather than inventing a new one (e.g. a
per-base threshold dict) to satisfy a different part of the same
instruction. Document the interpretation instead of guessing silently.

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

## 2026-09-23 — Image library storage kept fully independent of wilt-watch/drama-level

The image library task (local photo storage/retention, portal library
pages, HA thumbnail passthrough - `modules/Camera Module/
image_library.py`) raised a real design ambiguity before any code was
written: wilt-watch and drama-level already persist their own images
per base (`wilt_reference_<base_id>.json`, `drama_previous_<base_id>.json`
- grayscale, cropped, stored for structural-comparison math only, see
`image_compare.py`). The new library's "Most Recent" slot is described
in plain language as "the same image already used internally for
drama-level's rolling comparison" - which reads like it should share
storage with drama-level's own "previous capture" file rather than
duplicate it.

Asked the user directly rather than guessing, since either answer would
require touching two already-shipped, merged, tested modules
(`wilt_watch.py`, `drama_level.py`) if the shared-storage reading was
correct. **Answer: keep those modules completely unchanged - no
refactor, no touching their persistence format.** `image_library.py`
is a fully independent storage system with its own real (full-color)
JPEG files and its own metadata index. Both systems read from the same
original capture at the moment it's taken, then each persists its own
representation for its own consumer - wilt-watch/drama-level for their
own grayscale structural-diff math, the library for a human
viewing/downloading a real photo. `image_library.py` never reads or
writes wilt-watch's/drama-level's files, and they never read or write
the library's.

**Practice this reinforces:** when a task description's plain-language
summary of "reuse this" or "the same image already used for X" would,
if taken literally, require refactoring an already-shipped, tested
module outside the current task's stated scope, ask before assuming
that's what's wanted - a wrong guess here would have meant reopening
and re-testing two modules that were previously reviewed and merged
under a different task, for no benefit the user actually asked for.
