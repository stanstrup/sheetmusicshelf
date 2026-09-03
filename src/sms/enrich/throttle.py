"""Outbound rate limiting for the public metadata services.

MusicBrainz allows roughly one request a second per client and blocks those
that ignore it.  Spacing requests *within* one lookup is not enough: several
lookups in a row, or a retry storm, still add up to a burst.  This gate is
process-wide, so every request to a host waits its turn no matter which code
path issued it.

The breaker exists for the same reason.  A 503 from MusicBrainz means "you are
going too fast"; answering it with more requests is precisely wrong, so after a
run of them the host is left alone for a while.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

log = logging.getLogger("sms.enrich.throttle")


@dataclass
class HostGate:
    """Minimum spacing and a cooling-off period for one host."""

    name: str
    min_interval: float
    #: Consecutive rate-limit responses before the host is left alone.
    breaker_after: int = 3
    #: How long to stand down once the breaker trips.
    cool_off: float = 120.0

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _last: float = 0.0
    _strikes: int = 0
    _blocked_until: float = 0.0

    def wait(self) -> None:
        """Block until this host may be called again."""
        with self._lock:
            now = time.monotonic()
            if now < self._blocked_until:
                pause = self._blocked_until - now
                log.info("%s: standing down for %.0fs", self.name, pause)
                time.sleep(pause)
                now = time.monotonic()
            gap = now - self._last
            if gap < self.min_interval:
                time.sleep(self.min_interval - gap)
            self._last = time.monotonic()

    def ok(self) -> None:
        with self._lock:
            self._strikes = 0

    def throttled(self, retry_after: float | None = None) -> None:
        """Record a rate-limit response and, if they persist, stand down."""
        with self._lock:
            self._strikes += 1
            if self._strikes >= self.breaker_after:
                pause = max(retry_after or 0.0, self.cool_off)
                self._blocked_until = time.monotonic() + pause
                self._strikes = 0
                log.warning(
                    "%s: %d rate-limit responses; leaving it alone for %.0fs",
                    self.name, self.breaker_after, pause,
                )

    @property
    def standing_down(self) -> bool:
        return time.monotonic() < self._blocked_until


#: MusicBrainz publishes one request per second; leave headroom.
MUSICBRAINZ = HostGate("musicbrainz.org", min_interval=1.2)
#: IMSLP publishes no limit, but it is a small volunteer-run site.
IMSLP = HostGate("imslp.org", min_interval=0.6, breaker_after=4, cool_off=60.0)
#: Wikimedia is large and tolerant, but asks to be identified.
WIKIMEDIA = HostGate("wikimedia", min_interval=0.2, breaker_after=5, cool_off=30.0)


def gate_for(url: str) -> HostGate | None:
    if "musicbrainz.org" in url:
        return MUSICBRAINZ
    if "imslp.org" in url:
        return IMSLP
    if "wikipedia.org" in url or "wikidata.org" in url or "wikimedia.org" in url:
        return WIKIMEDIA
    return None
