"""
boot.py - Runs once, automatically, before main.py (standard MicroPython
boot sequence - not something this code needs to invoke itself).

Deliberately minimal. The one thing that genuinely needs to happen
before the asyncio event loop starts - the button boot-hold check for
re-entering WiFi setup mode (see core/wifi.py's
check_setup_hold_at_boot) - is blocking and takes up to a few seconds,
so it lives at the top of main.py instead, not here. Splitting "runs
before main" logic across two files for no reason would just make the
startup sequence harder to follow.
"""
