"""
boot.py - Runs once, automatically, before main.py (standard MicroPython
boot sequence - not something this code needs to invoke itself).

One real thing does need to happen here: adding /core, /drivers, and
/web to sys.path. main.py and every file within those directories use
flat imports (e.g. "import wifi", "import display", "import pins") on
the assumption that each other's directories are already searchable -
without this, MicroPython's default sys.path (roughly ['', '/lib'])
only resolves imports at the filesystem root, and every cross-directory
import in this project would fail immediately with ImportError the
moment main.py actually tried to run on real hardware. This was masked
during development by test harnesses that manually replicated this
sys.path setup themselves (see mainpy_test/run_smoke_test.py) rather
than exercising the real boot.py - caught only when adding version.py
(a root-level module imported from core/mqtt_client.py) prompted a
closer look at how cross-directory imports were actually supposed to
work in the first place.

The boot-hold check for re-entering WiFi setup mode (see core/wifi.py's
check_setup_hold_at_boot) stays in main.py, not here, despite also
being "runs before main" logic - it's blocking and takes up to a few
seconds, which is a poor fit for boot.py's job of being fast, minimal
startup plumbing.
"""

import sys

for _dir in ("/core", "/drivers", "/web"):
    if _dir not in sys.path:
        sys.path.append(_dir)
