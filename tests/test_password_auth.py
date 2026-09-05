"""Signing in with one shared password.

The middle setting. Authentik is right for a stack that already runs it and far
too much for one person with a tablet; SMS_AUTH_DISABLED is a development
convenience that must never face a network. This is what almost everybody
self-hosting this actually wants, and it has to be got right rather than
approximately right.
"""

from __future__ import annotations

import pytest

from sms import config
from sms.auth import PASSWORD_SUBJECT, password_matches


@pytest.fixture
def with_password(monkeypatch):
    monkeypatch.setenv("SMS_PASSWORD", "the right password")
    monkeypatch.delenv("SMS_AUTH_DISABLED", raising=False)
    monkeypatch.delenv("SMS_OIDC_ISSUER", raising=False)
    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


class TestCheckingIt:
    def test_the_right_one(self, with_password):
        assert password_matches("the right password")

    def test_a_wrong_one(self, with_password):
        assert not password_matches("the wrong password")

    def test_surrounding_space_is_forgiven(self, with_password):
        """Tablets add a space after a pasted password more often than not."""
        assert password_matches("  the right password  ")

    def test_a_prefix_is_not_enough(self, with_password):
        assert not password_matches("the right")

    def test_empty_never_matches(self, with_password):
        assert not password_matches("")

    def test_nothing_matches_when_none_is_set(self, monkeypatch):
        monkeypatch.setenv("SMS_PASSWORD", "")
        config.get_settings.cache_clear()
        try:
            # Otherwise an unconfigured server would let an empty form in.
            assert not password_matches("")
            assert not password_matches("anything")
        finally:
            config.get_settings.cache_clear()


class TestWhichModeIsOn:
    def test_a_password_alone_turns_it_on(self, with_password):
        assert config.get_settings().password_enabled

    def test_authentik_wins_when_both_are_set(self, monkeypatch):
        """An identity provider knows who each person is; a shared secret
        cannot, so it does not get to override one."""
        monkeypatch.setenv("SMS_PASSWORD", "shared")
        monkeypatch.setenv("SMS_OIDC_ISSUER", "https://auth.example.org/application/o/sms/")
        monkeypatch.setenv("SMS_OIDC_CLIENT_ID", "sms")
        config.get_settings.cache_clear()
        try:
            settings = config.get_settings()
            assert settings.oidc_enabled
            assert not settings.password_enabled
        finally:
            config.get_settings.cache_clear()

    def test_no_password_leaves_it_off(self, monkeypatch):
        monkeypatch.setenv("SMS_PASSWORD", "   ")
        config.get_settings.cache_clear()
        try:
            assert not config.get_settings().password_enabled
        finally:
            config.get_settings.cache_clear()


class TestTheSessionMarker:
    def test_it_cannot_be_confused_with_an_oidc_subject(self):
        """The session holds a subject; a password sign-in has no account
        behind it, so its marker must be something no provider could issue."""
        assert ":" in PASSWORD_SUBJECT
        assert PASSWORD_SUBJECT.startswith("password:")
