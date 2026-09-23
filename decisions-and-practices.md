# Decisions & Practices

A running log of project-level decisions and conventions that don't fit
neatly into [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) (documents
*how the system works*) or [`README.md`](README.md) (documents
*current status*) - things decided along the way that future
contributors should know were deliberate, not accidental or
overlooked. Newest entries at the top.

---

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
