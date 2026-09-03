"""Outbound politeness: identify, space, and stand down.

MusicBrainz blocks clients that ignore this, so these are not cosmetic.
"""

from __future__ import annotations

import time

import pytest

from sms.config import Settings
from sms.enrich.throttle import HostGate, gate_for


class TestContact:
    @pytest.mark.parametrize("contact", ["jan@example.com", "https://example.org/shelf"])
    def test_an_address_or_url_is_a_contact(self, contact):
        settings = Settings(contact=contact)
        assert settings.has_contact
        assert contact in settings.user_agent
        assert settings.user_agent.startswith("SheetMusicShelf/")

    @pytest.mark.parametrize("contact", ["", "   ", "unknown", "none", "me"])
    def test_anything_unreachable_is_not(self, contact):
        # A placeholder is worse than nothing: it looks compliant while being
        # unreachable, which is what gets a client blocked.
        assert not Settings(contact=contact).has_contact

    def test_no_contact_means_no_request(self):
        with pytest.raises(RuntimeError, match="SMS_CONTACT"):
            Settings(contact="").user_agent


class TestGateRouting:
    @pytest.mark.parametrize("url,expected", [
        ("https://musicbrainz.org/ws/2/work", "musicbrainz.org"),
        ("https://imslp.org/api.php", "imslp.org"),
        ("https://en.wikipedia.org/w/api.php", "wikimedia"),
        ("https://www.wikidata.org/w/api.php", "wikimedia"),
    ])
    def test_every_service_has_a_gate(self, url, expected):
        gate = gate_for(url)
        assert gate is not None and gate.name == expected

    def test_unknown_hosts_are_ungated(self):
        assert gate_for("https://example.com/") is None


class TestSpacing:
    def test_requests_are_spaced(self):
        gate = HostGate("test", min_interval=0.05)
        start = time.monotonic()
        for _ in range(4):
            gate.wait()
        # Three gaps between four slots.
        assert time.monotonic() - start >= 0.05 * 3

    def test_spacing_is_global_not_per_call(self):
        # The bug this guards: sleeping inside one lookup spaced its own
        # requests but not the gap to the next lookup, so a batch still burst.
        gate = HostGate("test", min_interval=0.05)
        gate.wait()
        start = time.monotonic()
        gate.wait()
        assert time.monotonic() - start >= 0.04


class TestBreaker:
    def test_repeated_rate_limits_stand_the_host_down(self):
        gate = HostGate("test", min_interval=0.0, breaker_after=3, cool_off=30.0)
        assert not gate.standing_down
        for _ in range(3):
            gate.throttled()
        assert gate.standing_down

    def test_success_clears_the_count(self):
        gate = HostGate("test", min_interval=0.0, breaker_after=3, cool_off=30.0)
        gate.throttled()
        gate.throttled()
        gate.ok()
        gate.throttled()
        assert not gate.standing_down

    def test_retry_after_is_honoured_when_longer(self):
        gate = HostGate("test", min_interval=0.0, breaker_after=1, cool_off=5.0)
        gate.throttled(retry_after=3600.0)
        assert gate.standing_down
