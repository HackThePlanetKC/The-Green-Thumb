"""
camera_capture.py - "Take one photo right now" primitive.

Deliberately narrow: this is NOT capture scheduling (when photos get
taken periodically, exposure/flash coordination, storage rotation -
still not built, see README.md "Remaining"). It's the one missing
piece needed by two things that DO need a real photo today even
though the scheduler doesn't exist yet: the grid layout setup page
(item 2 of the grid/settings task - a reference still to overlay the
grid onto and assign cells against) and wilt-watch's reference-image
capture (wilt_watch.py). Both just need "grab a frame now and hand me
the file", not a scheduled/recurring capture.

Shells out to rpicam-still (the current libcamera-based CLI on
Raspberry Pi OS Bookworm+; the same tool's older name, libcamera-still,
is a symlink to it on those releases) - same "shell out to the Pi OS's
own tool" pattern as wifi_manager.py's nmcli usage, for the same
reason: no extra Python camera dependency to manage (picamera2 would
work too, but a single blocking CLI call is simpler for a one-shot
capture and matches this module's existing style better than pulling
in a new library for one function).
"""

import subprocess


def _run(args, timeout_s=15):
    """
    The one place this module's camera CLI call goes through -
    injectable so tests never shell out to a real camera. Mirrors
    wifi_manager.py's _run(): a timeout is surfaced as a non-zero-
    equivalent failure (returncode -1) rather than letting
    subprocess.TimeoutExpired propagate, so callers only handle one
    failure shape.
    """
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args=args, returncode=-1, stdout="", stderr="timed out")


def capture_still(output_path, run=_run, timeout_s=15):
    """
    Captures one still image to output_path (JPEG). Returns True on
    success, False on any failure (no camera attached, rpicam-still
    not installed, timeout) - the caller decides how to surface that
    (e.g. the grid setup page shows an error instead of the reference
    image).

    "-n" (no preview window) and "-t 1" (minimal timing delay, this
    isn't an interactive preview session) keep this a fast, headless,
    one-shot capture suitable for calling from a web request handler.
    """
    result = run(["rpicam-still", "-n", "-t", "1", "-o", output_path], timeout_s)
    return result.returncode == 0
