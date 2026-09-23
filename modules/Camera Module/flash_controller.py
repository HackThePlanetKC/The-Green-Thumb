"""
flash_controller.py - Pre-capture low-light flash trigger.

Called once per scheduled capture, before the shot: reads the current
light level, and if it's below config.flash.low_light_threshold, turns
the ring's white channel on for exposure_s - timed to the camera's
exposure, not held on before or after - then off again. Above
threshold, no flash fires; capture proceeds on ambient light alone.

Capture scheduling and the light sensor driver itself are out of scope
for this change (see README.md "Remaining") - this module only owns
the "should I flash, and for how long" decision, given a light reading
and an exposure duration handed to it by whatever ends up driving the
actual capture.
"""

import time


def maybe_flash(light_level, threshold, exposure_s, ring, brightness=0.5, sleep=time.sleep):
    """
    Returns True if a flash was fired, False if the ambient light was
    already at or above threshold and no flash was needed.

    light_level: current reading from this module's light sensor, same
        units as `threshold` (see config.py's low_light_threshold
        comment - unit depends on which sensor driver eventually
        supplies this).
    threshold: config.flash.low_light_threshold (or an override) - the
        caller reads config, this function doesn't load it itself, so
        it stays a pure decision function rather than also owning
        config I/O.
    exposure_s: how long to hold the flash on, matched to the camera's
        exposure time for this capture - not a fixed constant here,
        since exposure can vary shot to shot (e.g. auto-exposure).
    ring: a Ring instance (see ring.py), or anything duck-typed with
        set_white()/off() - deliberately not type-checked, so tests can
        pass a plain fake instead of real hardware.
    brightness: forwarded to ring.set_white() - defaults to a sane
        starting level, but callers should normally pass
        config.flash.brightness explicitly rather than rely on this
        default, same reasoning as threshold above.
    sleep: injected for testability - defaults to the real
        time.sleep, but tests pass a fake so "hold the flash on for
        exposure_s" doesn't actually block the test suite.
    """
    if light_level >= threshold:
        return False

    ring.set_white(brightness)
    try:
        sleep(exposure_s)
    finally:
        # finally, not just a call after sleep() returns - a flash that
        # fires but never turns back off (e.g. an exception raised
        # during the exposure wait) would leave the ring lit
        # indefinitely, exactly what "not held on" is guarding against.
        ring.off()
    return True
