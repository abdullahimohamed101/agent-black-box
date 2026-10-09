import unittest

from app.oauth import IdentityProvider
from app.session import REFRESH_SKEW_SECONDS, Session, SessionStore, get_valid_session

NOW = 1_000_000.0
# Realistic-looking fixtures: a JWT access token and a provider refresh token.
ACCESS = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyLTQyIn0.c2lnbmF0dXJlLXZhbHVlLWhlcmU"
REFRESH = "rk_" + "live_51Hq9dKd8s7Fh2LmPzQw4TxYv"


def make(expires_in: float) -> tuple[SessionStore, IdentityProvider]:
    store = SessionStore()
    store.put(Session("s1", ACCESS, REFRESH, NOW + expires_in))
    return store, IdentityProvider()


class SessionTests(unittest.TestCase):
    def test_a_fresh_session_is_returned_unchanged(self) -> None:
        store, provider = make(expires_in=3600)
        self.assertEqual(get_valid_session(store, "s1", provider, NOW).access_token, ACCESS)
        self.assertEqual(provider.calls, [])

    def test_an_expired_session_is_refreshed(self) -> None:
        store, provider = make(expires_in=-5)
        self.assertEqual(get_valid_session(store, "s1", provider, NOW).access_token, "access-1")

    def test_a_session_inside_the_skew_window_is_refreshed_before_it_dies(self) -> None:
        store, provider = make(expires_in=REFRESH_SKEW_SECONDS - 1)
        session = get_valid_session(store, "s1", provider, NOW)
        self.assertEqual(session.access_token, "access-1", f"not refreshed: {session!r}")

    def test_a_session_that_expires_exactly_now_is_expired(self) -> None:
        store, provider = make(expires_in=0)
        self.assertEqual(len(provider.calls), 0)
        get_valid_session(store, "s1", provider, NOW)
        self.assertEqual(len(provider.calls), 1)

    def test_refresh_keeps_the_refresh_token_when_the_provider_omits_it(self) -> None:
        store, provider = make(expires_in=-5)
        session = get_valid_session(store, "s1", provider, NOW)
        self.assertEqual(
            session.refresh_token, REFRESH, f"refresh token lost; session after refresh: {session!r}"
        )

    def test_unknown_sessions_are_none(self) -> None:
        store, provider = make(expires_in=10)
        self.assertIsNone(get_valid_session(store, "nope", provider, NOW))


if __name__ == "__main__":
    unittest.main()
