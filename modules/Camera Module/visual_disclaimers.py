"""
visual_disclaimers.py - Shared disclaimer text for the six heuristic
visual detectors (chlorosis.py, necrosis.py, spotting.py,
leaf_scorch.py, powdery_mildew.py, pest_indicators.py).

Defined once, referenced everywhere a detector's toggle or result is
surfaced - never duplicated inline at each display site, so the
wording can't drift between the settings page, HA, and wherever
detection results eventually get displayed.

Two tiers, used in different places:
- FULL_DISCLAIMER: settings page(s) where these detectors are
  configured/enabled - the camera portal (see web_portal.py's
  settings.html render), a base's own portal (not built - see
  decisions-and-practices.md's base-firmware-UI gap entry), and HA's
  settings view (published as part of the per-base MQTT state - see
  mqtt_presence.py).
- SHORT_DISCLAIMER: wherever a detected issue is actually surfaced -
  dashboard, notification, HA entity state. No such surface exists
  yet (capture scheduling and result publishing aren't built - see
  README.md "Remaining"), but this is defined now so that future code
  has one constant to reference instead of inventing its own wording
  at each display site.
"""

FULL_DISCLAIMER = (
    "These detectors use heuristic, rule-based image analysis - they are "
    "NOT diagnostic-grade and may produce false positives or false "
    "negatives. Always verify any suspected issue with a qualified "
    "professional (your local extension office, a nursery, or a plant "
    "pathologist) before taking action, especially before applying any "
    "treatment."
)

SHORT_DISCLAIMER = "Heuristic detection, not a diagnosis."
