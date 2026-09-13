"""
core/ntp.py - NTP time sync with a configurable fixed UTC offset for
local time.

MicroPython's ntptime.settime() sets the system RTC to UTC and has no
timezone or DST support built in. This module layers a fixed UTC offset
(config['timezone']['utc_offset_hours']) on top to derive local time for
night mode and the daily light-hours midnight rollover. There is no
automatic DST adjustment - if the user's region observes DST, the
offset needs manual updating twice a year. A full timezone/DST rules
database is far more than this project needs, so this is a documented
limitation rather than something worked around.

ntptime.settime() is a blocking socket call, but typically well under a
second - unlike the ~15s WiFi connection wait, a single sync attempt
briefly blocking the event loop is an accepted tradeoff here rather than
building a non-blocking NTP client from scratch. The wait BETWEEN sync
attempts (backoff, and the 24h resync interval) uses asyncio.sleep so
that part doesn't block anything.
"""

import time

import asyncio
import ntptime

_MAX_BACKOFF_S = 300  # 5 min


class NtpSync:
    def __init__(self, utc_offset_hours=0, resync_interval_s=86400):
        self._utc_offset_hours = utc_offset_hours
        self._resync_interval_s = resync_interval_s
        self._synced = False
        self._backoff_s = 5

    def set_utc_offset_hours(self, offset_hours):
        """Call if the user changes timezone.utc_offset_hours via set_config at runtime."""
        self._utc_offset_hours = offset_hours

    def is_synced(self):
        """
        False until the first successful sync. Callers that care about
        correctness (e.g. display.py's night mode) should check this
        before trusting local_time() - an unsynced RTC would otherwise
        silently produce a wrong night-mode window rather than an
        obviously-wrong one.
        """
        return self._synced

    def sync_once(self):
        """
        One blocking NTP sync attempt. Returns True on success, False on
        failure (ntptime raises OSError on network error/timeout - caught
        here so a bad sync doesn't crash the caller).
        """
        try:
            ntptime.settime()
            self._synced = True
            self._backoff_s = 5  # reset backoff after a real success
            return True
        except OSError:
            return False

    def local_time(self):
        """
        Returns (hour, minute) in local time, applying the configured
        UTC offset to the RTC (which ntptime sets to UTC). This is the
        shape drivers/display.py's now_provider callable expects.

        Returns (0, 0) if never successfully synced - see is_synced().
        """
        if not self._synced:
            return (0, 0)
        utc = time.localtime()  # RTC reads as UTC, since that's what ntptime set
        offset_s = int(self._utc_offset_hours * 3600)
        local_epoch = time.mktime(utc) + offset_s
        local = time.localtime(local_epoch)
        return (local[3], local[4])  # (hour, minute)

    def local_date(self):
        """
        Returns (year, month, day) in local time - used for detecting
        the daily midnight rollover in core/light_tracker.py (not yet
        written), so "today" is based on local time, not UTC.
        """
        if not self._synced:
            return (0, 0, 0)
        utc = time.localtime()
        offset_s = int(self._utc_offset_hours * 3600)
        local = time.localtime(time.mktime(utc) + offset_s)
        return (local[0], local[1], local[2])

    async def sync_forever(self):
        """
        Background asyncio task: syncs immediately, then re-syncs every
        resync_interval_s to correct RTC drift over long uptimes. On
        failure, retries with capped exponential backoff rather than
        hammering the NTP server or blocking indefinitely.
        """
        while True:
            success = self.sync_once()
            if success:
                self._backoff_s = 5
                await asyncio.sleep(self._resync_interval_s)
            else:
                wait_s = self._backoff_s
                self._backoff_s = min(self._backoff_s * 2, _MAX_BACKOFF_S)
                await asyncio.sleep(wait_s)
