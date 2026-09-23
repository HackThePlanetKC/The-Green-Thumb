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
exists so far (ring driver, flash trigger logic, config) - capture
scheduling, the light sensor driver it reads from, and MQTT
discovery/publishing don't exist yet. See this module's README.md for
current status.
"""

VERSION = "0.1.0"
