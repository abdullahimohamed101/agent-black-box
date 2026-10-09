"""Sign-in with OpenID Connect and the sessions it creates, end to end against a fake provider.

Every way a login can be forged or confused is a test here: the callback must bind `state` to the
browser's cookie, refuse every bad ID token, never re-bind a linked identity by email, and leave
no user or session behind when it refuses.
"""

import logging
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from abb_api.auth.repository import UserRepository
from abb_api.core.config import Settings
from abb_api.ingestion.ratelimit import InMemoryRateLimiter
from tests.api_fixtures import Api, Tick, build_api, person
from tests.auth_helpers import add_member, mint_session
from tests.conftest import make_settings
from tests.fake_oidc import FakeOidc
from tests.test_runs_api import error

ORIGIN = "http://localhost:3000"
ISSUER = "http://idp.test"
CLIENT_ID = "abb-test"
REDIRECT = f"{ORIGIN}/api/auth/callback"
SESSION_COOKIE = "abb_session"


@pytest.fixture
def fake() -> FakeOidc:
    tick = Tick()
    return FakeOidc(issuer=ISSUER, client_id=CLIENT_ID, redirect_uris=(REDIRECT,), clock=tick)


def oidc_settings(database_url: str, **extra: Any) -> Settings:
    return make_settings(
        database_url,
        oidc_issuer=ISSUER,
        oidc_client_id=CLIENT_ID,
        web_origin=ORIGIN,
        **extra,
    )


@pytest.fixture
async def auth(
    database_url: str, runtime_database_url: str, engine: AsyncEngine, fake: FakeOidc
) -> AsyncIterator[Api]:
    assert isinstance(fake.clock, Tick)
    async for instance in build_api(
        database_url,
        engine,
        settings=oidc_settings(database_url),
        clock=fake.clock,
        app_options={"oidc_transport": fake.transport()},
    ):
        yield instance


def cookies_set(response: httpx.Response) -> dict[str, str]:
    """name -> full Set-Cookie line, for every cookie the response sets."""
    return {line.split("=", 1)[0]: line for line in response.headers.get_list("set-cookie")}


def cookie_value(line: str) -> str:
    return line.split(";", 1)[0].split("=", 1)[1]


class Browser:
    """Just enough of a browser: it keeps the login cookie and the session cookie by hand."""

    def __init__(self, api: Api, fake: FakeOidc) -> None:
        self.api, self.fake = api, fake
        self.login_cookie: str | None = None
        self.session: str | None = None

    def cookie_header(self) -> dict[str, str]:
        pairs = []
        if self.login_cookie:
            pairs.append(f"abb_login={self.login_cookie}")
        if self.session:
            pairs.append(f"{SESSION_COOKIE}={self.session}")
        return {"cookie": "; ".join(pairs)} if pairs else {}

    async def start(self, return_to: str | None = None) -> httpx.Response:
        params = {"return_to": return_to} if return_to is not None else {}
        response = await self.api.client.get("/v1/auth/login", params=params)
        self.api.client.cookies.clear()  # cookies are kept by hand, as the web app would relay them
        if response.status_code == 302:
            self.login_cookie = cookie_value(cookies_set(response)["abb_login"])
        return response

    async def callback(
        self, url: str, *, cookie: bool | str | None = True, **override: str
    ) -> httpx.Response:
        query = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()} | override
        headers = {}
        if cookie is True and self.login_cookie:
            headers["cookie"] = f"abb_login={self.login_cookie}"
        elif isinstance(cookie, str):
            headers["cookie"] = f"abb_login={cookie}"
        response = await self.api.client.get("/v1/auth/callback", params=query, headers=headers)
        self.api.client.cookies.clear()
        cookies = cookies_set(response)
        if SESSION_COOKIE in cookies and response.status_code == 302:
            self.session = cookie_value(cookies[SESSION_COOKIE])
        self.login_cookie = None
        return response

    async def sign_in(self, email: str, **kwargs: Any) -> httpx.Response:
        started = await self.start(kwargs.pop("return_to", None))
        assert started.status_code == 302, started.text
        return await self.callback(self.fake.approve(started.headers["location"], email, **kwargs))

    async def get(self, path: str, **headers: str) -> httpx.Response:
        return await self.api.client.get(path, headers={**self.cookie_header(), **headers})

    async def post(self, path: str, origin: str | None = ORIGIN) -> httpx.Response:
        headers = self.cookie_header()
        if origin is not None:
            headers["origin"] = origin
        return await self.api.client.post(path, headers=headers)


async def counts(engine: AsyncEngine) -> tuple[int, int]:
    async with engine.connect() as conn:
        users = (await conn.execute(text("SELECT count(*) FROM users"))).scalar_one()
        sessions = (await conn.execute(text("SELECT count(*) FROM sessions"))).scalar_one()
    return int(users), int(sessions)


# ------------------------------------------------------------------ the happy path


async def test_a_full_login_creates_a_user_and_a_session_and_lands_on_return_to(
    auth: Api, fake: FakeOidc
) -> None:
    browser = Browser(auth, fake)
    started = await browser.start("/w/acme/projects/all")
    assert started.status_code == 302
    location = urlsplit(started.headers["location"])
    query = parse_qs(location.query)
    assert f"{location.scheme}://{location.netloc}{location.path}" == f"{ISSUER}/authorize"
    assert query["code_challenge_method"] == ["S256"] and query["redirect_uri"] == [REDIRECT]
    assert query["client_id"] == [CLIENT_ID] and query["response_type"] == ["code"]
    login_cookie = cookies_set(started)["abb_login"]
    assert cookie_value(login_cookie) == query["state"][0]
    for attribute in ("HttpOnly", "SameSite=Lax", "Path=/api/auth", "Max-Age=600"):
        assert attribute in login_cookie
    assert "Secure" not in login_cookie  # http://localhost development origin

    done = await browser.callback(fake.approve(started.headers["location"], "Ada@Example.com"))
    assert done.status_code == 302 and done.headers["location"] == "/w/acme/projects/all"
    cookies = cookies_set(done)
    session = cookies[SESSION_COOKIE]
    for attribute in ("HttpOnly", "SameSite=Lax", "Path=/", f"Max-Age={168 * 3600}"):
        assert attribute in session
    assert "Max-Age=0" in cookies["abb_login"]  # the login cookie is spent
    assert done.headers["cache-control"] == "no-store"

    me = await browser.get("/v1/me")
    assert me.status_code == 200
    body = me.json()
    assert body["user"]["email"] == "ada@example.com" and body["user"]["id"].startswith("usr_")
    assert body["memberships"] == []  # invite-only: a stranger who signs in belongs nowhere


async def test_me_lists_memberships_with_the_actions_their_roles_grant(
    auth: Api, fake: FakeOidc
) -> None:
    await add_member(auth.engine, auth.tenant.context.workspace_id, "ada@example.com", "VIEWER")
    await add_member(auth.engine, auth.other.context.workspace_id, "ada@example.com", "OWNER")
    browser = Browser(auth, fake)
    assert (await browser.sign_in("ada@example.com")).status_code == 302
    memberships = (await browser.get("/v1/me")).json()["memberships"]
    by_slug = {m["workspace"]["slug"]: m for m in memberships}
    assert set(by_slug) == {"acme", "globex"}
    assert by_slug["acme"]["role"] == "VIEWER"
    assert "run.read" in by_slug["acme"]["permissions"]
    assert "payload.read" not in by_slug["acme"]["permissions"]
    assert "api_key.create" in by_slug["globex"]["permissions"]


async def test_a_pre_provisioned_user_is_linked_on_first_login(auth: Api, fake: FakeOidc) -> None:
    user = await add_member(
        auth.engine, auth.tenant.context.workspace_id, "ada@example.com", "OWNER"
    )
    assert user.provider_subject is None
    browser = Browser(auth, fake)
    assert (await browser.sign_in("ADA@example.com", subject="sub-ada")).status_code == 302
    async with auth.engine.connect() as conn:
        linked = await UserRepository(conn).get(user.id)
    assert linked and linked.provider == ISSUER and linked.provider_subject == "sub-ada"
    assert linked.email_verified_at is not None and linked.last_login_at is not None
    assert (await counts(auth.engine))[0] == 1  # no duplicate user was created


async def test_the_subject_decides_so_an_email_change_at_the_provider_is_followed(
    auth: Api, fake: FakeOidc
) -> None:
    browser = Browser(auth, fake)
    await browser.sign_in("old@example.com", subject="sub-1")
    again = Browser(auth, fake)
    assert (await again.sign_in("new@example.com", subject="sub-1")).status_code == 302
    assert (await again.get("/v1/me")).json()["user"]["email"] == "new@example.com"
    assert (await counts(auth.engine))[0] == 1  # the same person, not a second user


async def test_an_email_change_onto_another_users_address_changes_nothing(
    auth: Api, fake: FakeOidc
) -> None:
    await Browser(auth, fake).sign_in("one@example.com", subject="sub-1")
    await Browser(auth, fake).sign_in("two@example.com", subject="sub-2")
    refused = await Browser(auth, fake).sign_in("two@example.com", subject="sub-1")
    error(refused, 401, "LOGIN_FAILED")
    again = Browser(auth, fake)
    await again.sign_in("one@example.com", subject="sub-1")
    assert (await again.get("/v1/me")).json()["user"]["email"] == "one@example.com"


async def test_a_reassigned_address_cannot_accept_the_new_holders_invitation(
    auth: Api, fake: FakeOidc
) -> None:
    """Review F2: acceptance compares the current verified email, not the first login's."""
    alice = Browser(auth, fake)
    await alice.sign_in("alice@corp.test", subject="sub-alice")
    # The provider renames Alice's account and hands her old address to Bob.
    await alice.sign_in("alice.2@corp.test", subject="sub-alice")
    admin = await person(auth, "admin@acme.test", "ADMIN")
    sent = await auth.client.post(
        "/v1/invitations", json={"email": "alice@corp.test", "role": "VIEWER"}, headers=admin
    )
    token = sent.json()["link"].split("#", 1)[1]
    bob = Browser(auth, fake)
    assert (await bob.sign_in("alice@corp.test", subject="sub-bob")).status_code == 302

    def accept(browser: Browser) -> Any:
        return auth.client.post(
            "/v1/invitations/accept",
            json={"token": token},
            headers={**browser.cookie_header(), "origin": ORIGIN},
        )

    error(await accept(alice), 403, "INVITATION_EMAIL_MISMATCH")
    assert (await accept(bob)).status_code == 200


# ------------------------------------------------------------------ login CSRF and state


async def test_a_callback_without_the_browsers_login_cookie_is_refused(
    auth: Api, fake: FakeOidc
) -> None:
    attacker = Browser(auth, fake)
    started = await attacker.start()
    url = fake.approve(started.headers["location"], "attacker@example.com")
    victim = Browser(auth, fake)  # follows the attacker's callback URL; has no abb_login cookie
    for cookie in (False, "a" * 43):  # none at all / a different value
        refused = await victim.callback(url, cookie=cookie)
        error(refused, 400, "LOGIN_FAILED")
        assert SESSION_COOKIE not in cookies_set(refused)
    assert await counts(auth.engine) == (0, 0)
    # The attacker's own browser can still finish: refusing the victim did not burn the state.
    assert (await attacker.callback(url)).status_code == 302


async def test_a_state_can_only_be_used_once(auth: Api, fake: FakeOidc) -> None:
    browser = Browser(auth, fake)
    started = await browser.start()
    url = fake.approve(started.headers["location"], "ada@example.com")
    cookie = browser.login_cookie
    assert (await browser.callback(url)).status_code == 302
    error(await browser.callback(url, cookie=cookie), 400, "LOGIN_FAILED")


async def test_an_expired_login_is_refused(auth: Api, fake: FakeOidc) -> None:
    browser = Browser(auth, fake)
    started = await browser.start()
    url = fake.approve(started.headers["location"], "ada@example.com")
    auth.clock.now += timedelta(minutes=11)
    error(await browser.callback(url), 400, "LOGIN_FAILED")
    assert await counts(auth.engine) == (0, 0)


async def test_an_unknown_state_is_refused_even_with_a_matching_cookie(
    auth: Api, fake: FakeOidc
) -> None:
    browser = Browser(auth, fake)
    state = "s" * 43
    refused = await browser.callback(f"{REDIRECT}?code=x&state={state}", cookie=state)
    error(refused, 400, "LOGIN_FAILED")


async def test_the_provider_reporting_an_error_is_never_echoed(
    auth: Api, fake: FakeOidc, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    browser = Browser(auth, fake)
    started = await browser.start()
    url = fake.approve(started.headers["location"], "ada@example.com")
    refused = await browser.callback(
        url, error="access_denied", error_description="<script>alert(1)</script>"
    )
    error(refused, 400, "LOGIN_FAILED")
    assert "script" not in refused.text and "access_denied" not in refused.text
    assert "script" not in caplog.text and "access_denied" not in caplog.text
    assert await counts(auth.engine) == (0, 0)


# ------------------------------------------------------------------ return_to


@pytest.mark.parametrize(
    "target",
    ["https://evil.example", "//evil.example", "/\\evil.example", "/%2F%2Fevil.example",
     "/w/x%0d%0aSet-Cookie:a=b", "javascript:alert(1)", "/login", "/api/auth/logout",
     "/w/a/../../etc", "/w/a#frag", "/w/a?x=%2F", "w/a", "/w/" + "a" * 65, "/w/@evil.example"],
)  # fmt: skip
async def test_return_to_outside_the_applications_own_routes_is_refused_at_login(
    auth: Api, fake: FakeOidc, target: str
) -> None:
    refused = await Browser(auth, fake).start(target)
    error(refused, 422, "RETURN_TO_INVALID")
    assert "set-cookie" not in refused.headers
    async with auth.engine.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM login_states"))).scalar_one() == 0


@pytest.mark.parametrize(
    "target", ["/", "/w/acme", "/w/acme/projects/all", "/invite", "/w/a/b?x=1&y=2"]
)
async def test_application_routes_are_accepted_and_the_callback_redirects_only_to_them(
    auth: Api, fake: FakeOidc, target: str
) -> None:
    browser = Browser(auth, fake)
    started = await browser.start(target)
    url = fake.approve(started.headers["location"], "ada@example.com")
    # A callback cannot choose where it goes: a return_to in its query is ignored.
    done = await browser.callback(url, return_to="https://evil.example")
    assert done.status_code == 302 and done.headers["location"] == target


# ------------------------------------------------------------------ the ID token


@pytest.mark.parametrize(
    ("knob", "value"),
    [
        ("bad_nonce", True),
        ("bad_signature", True),
        ("unknown_kid", True),
        ("audience", "another-client"),
        ("authorized_party", "another-client"),
        ("issuer_claim", "http://evil.test"),
        ("expired", True),
        ("email_verified", False),
        ("email_verified", None),
        ("email_verified", "true"),
        ("omit_email", True),
        ("algorithm", "HS256"),
    ],
)
async def test_every_bad_id_token_is_refused_and_leaves_nothing_behind(
    auth: Api, fake: FakeOidc, knob: str, value: object, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    setattr(fake.knobs, knob, value)
    refused = await Browser(auth, fake).sign_in("ada@example.com")
    assert refused.status_code in (400, 401)
    assert refused.json()["error"]["code"] == "LOGIN_FAILED"
    assert SESSION_COOKIE not in cookies_set(refused)
    assert await counts(auth.engine) == (0, 0)
    assert any(r.getMessage() == "login failed" for r in caplog.records)


async def test_discovery_must_name_the_configured_issuer(auth: Api, fake: FakeOidc) -> None:
    fake.knobs.discovery_issuer = "http://other-idp.test"
    refused = await Browser(auth, fake).start()
    error(refused, 503, "AUTH_UNAVAILABLE")
    assert "set-cookie" not in refused.headers


async def test_discovery_endpoints_must_live_on_the_issuers_host(auth: Api, fake: FakeOidc) -> None:
    fake.knobs.discovery_extra = {"token_endpoint": "http://evil.test/token"}
    refused = await Browser(auth, fake).start()
    error(refused, 503, "AUTH_UNAVAILABLE")


async def test_a_key_rotation_is_followed_but_probing_unknown_keys_is_bounded(
    auth: Api, fake: FakeOidc
) -> None:
    assert (await Browser(auth, fake).sign_in("a@example.com")).status_code == 302
    base = fake.requests.count("/jwks")
    fake.rotate()  # the provider now signs with a key the cache has never seen
    # Within a minute of the last fetch the unknown key is refused without asking the provider.
    assert (await Browser(auth, fake).sign_in("b@example.com")).status_code == 401
    assert fake.requests.count("/jwks") == base
    auth.clock.now += timedelta(seconds=61)
    assert (await Browser(auth, fake).sign_in("b@example.com")).status_code == 302
    assert fake.requests.count("/jwks") == base + 1
    # An unpublished kid triggers at most one refetch per minute, however often it is tried.
    fake.knobs.unknown_kid = True
    auth.clock.now += timedelta(seconds=61)
    for _ in range(4):
        assert (await Browser(auth, fake).sign_in("c@example.com")).status_code == 401
    assert fake.requests.count("/jwks") == base + 2


# ------------------------------------------------------------------ identity linking


async def test_a_verified_email_cannot_take_over_a_user_linked_to_another_subject(
    auth: Api, fake: FakeOidc
) -> None:
    owner = await add_member(
        auth.engine, auth.tenant.context.workspace_id, "ceo@example.com", "OWNER"
    )
    assert (
        await Browser(auth, fake).sign_in("ceo@example.com", subject="sub-real")
    ).status_code == 302
    before = await counts(auth.engine)
    refused = await Browser(auth, fake).sign_in("ceo@example.com", subject="sub-impostor")
    error(refused, 401, "LOGIN_FAILED")
    assert SESSION_COOKIE not in cookies_set(refused)
    assert await counts(auth.engine) == before
    async with auth.engine.connect() as conn:
        unchanged = await UserRepository(conn).get(owner.id)
    assert unchanged and unchanged.provider_subject == "sub-real"


# ------------------------------------------------------------------ sessions


async def signed_in(auth: Api, fake: FakeOidc, email: str = "ada@example.com") -> Browser:
    browser = Browser(auth, fake)
    assert (await browser.sign_in(email)).status_code == 302
    return browser


async def test_a_missing_or_foreign_cookie_is_the_uniform_401(auth: Api, fake: FakeOidc) -> None:
    for headers in (
        {},
        {"cookie": f"{SESSION_COOKIE}=nope"},
        {"cookie": f"{SESSION_COOKIE}={'x' * 43}"},
    ):
        response = await auth.client.get("/v1/me", headers=headers)
        error(response, 401, "SESSION_INVALID")
        assert "set-cookie" not in response.headers


async def test_an_api_key_in_the_cookie_or_a_session_in_the_bearer_header_authenticates_nothing(
    auth: Api, fake: FakeOidc
) -> None:
    browser = await signed_in(auth, fake)
    key = auth.tokens["reader"]
    as_cookie = await auth.client.get("/v1/me", headers={"cookie": f"{SESSION_COOKIE}={key}"})
    error(as_cookie, 401, "SESSION_INVALID")
    as_bearer = await auth.client.get(
        "/v1/runs", headers={"authorization": f"Bearer {browser.session}"}
    )
    error(as_bearer, 401, "API_KEY_INVALID")
    # A session in the URL is just an unknown parameter.
    assert (await auth.client.get(f"/v1/me?session={browser.session}")).status_code == 401


async def test_the_bearer_header_wins_over_a_cookie(auth: Api, fake: FakeOidc) -> None:
    browser = await signed_in(auth, fake)
    both = await auth.client.get(
        "/v1/runs",
        headers={**browser.cookie_header(), "authorization": f"Bearer {auth.tokens['reader']}"},
    )
    assert both.status_code == 200  # judged as the key, not as the (workspace-less) person


async def test_sessions_expire_absolutely_and_when_idle_and_the_idle_clock_slides(
    auth: Api, fake: FakeOidc
) -> None:
    browser = await signed_in(auth, fake)
    for _ in range(8):  # activity every 20 hours keeps the 24-hour idle window open (160 hours)...
        auth.clock.now += timedelta(hours=20)
        assert (await browser.get("/v1/me")).status_code == 200
    auth.clock.now += timedelta(hours=20)  # ...but not past the absolute 168 hours
    error(await browser.get("/v1/me"), 401, "SESSION_INVALID")

    quiet = await signed_in(auth, fake, "quiet@example.com")
    auth.clock.now += timedelta(hours=25)  # idle for longer than 24 hours
    error(await quiet.get("/v1/me"), 401, "SESSION_INVALID")


async def test_logout_revokes_the_session_clears_the_cookie_and_is_idempotent(
    auth: Api, fake: FakeOidc
) -> None:
    browser = await signed_in(auth, fake)
    out = await browser.post("/v1/auth/logout")
    assert out.status_code == 200 and out.json() == {"ok": True}
    assert "Max-Age=0" in cookies_set(out)[SESSION_COOKIE]
    error(await browser.get("/v1/me"), 401, "SESSION_INVALID")
    again = await browser.post("/v1/auth/logout")  # the cookie is dead now: still a success
    assert again.status_code == 200
    nothing = await auth.client.post("/v1/auth/logout")
    assert nothing.status_code == 200
    assert (await auth.client.get("/v1/auth/logout")).status_code == 405  # no GET side effects


@pytest.mark.parametrize("origin", [None, "null", "http://evil.example", "http://localhost:3001"])
async def test_logout_by_a_foreign_origin_is_a_csrf_rejection(
    auth: Api, fake: FakeOidc, origin: str | None
) -> None:
    browser = await signed_in(auth, fake)
    error(await browser.post("/v1/auth/logout", origin=origin), 403, "CSRF_REJECTED")
    assert (await browser.get("/v1/me")).status_code == 200  # still signed in


async def test_a_revoked_session_stops_working_at_once(auth: Api, fake: FakeOidc) -> None:
    browser = await signed_in(auth, fake)
    async with auth.engine.begin() as conn:
        await conn.execute(text("UPDATE sessions SET revoked_at = now()"))
    error(await browser.get("/v1/me"), 401, "SESSION_INVALID")


async def test_database_trouble_during_session_validation_is_a_503_never_a_401(
    client_db_down: httpx.AsyncClient,
) -> None:
    response = await client_db_down.get(
        "/v1/me", headers={"cookie": f"{SESSION_COOKIE}={'x' * 43}"}
    )
    error(response, 503, "DEPENDENCY_UNAVAILABLE")


# ------------------------------------------------------------ cookie writes and workspaces


async def test_a_user_must_name_a_workspace_and_must_belong_to_it(
    auth: Api, fake: FakeOidc
) -> None:
    await add_member(auth.engine, auth.tenant.context.workspace_id, "ada@example.com", "VIEWER")
    browser = await signed_in(auth, fake)
    error(await browser.get("/v1/runs"), 400, "WORKSPACE_REQUIRED")
    acme, globex = auth.tenant.workspace_id, auth.other.workspace_id
    assert (await browser.get("/v1/runs", **{"x-abb-workspace": acme})).status_code == 200
    foreign = await browser.get("/v1/runs", **{"x-abb-workspace": globex})
    random_ws = await browser.get(
        "/v1/runs", **{"x-abb-workspace": "ws_01HZZZZZZZZZZZZZZZZZZZZZZZ"}
    )
    malformed = await browser.get("/v1/runs", **{"x-abb-workspace": "acme"})
    for refused in (foreign, random_ws, malformed):
        error(refused, 404, "WORKSPACE_NOT_FOUND")
    assert foreign.json()["error"]["message"] == random_ws.json()["error"]["message"]


async def test_an_api_key_may_only_name_its_own_workspace(auth: Api) -> None:
    headers = {"authorization": f"Bearer {auth.tokens['reader']}"}
    ok = await auth.client.get(
        "/v1/runs", headers={**headers, "x-abb-workspace": auth.tenant.workspace_id}
    )
    assert ok.status_code == 200
    other = await auth.client.get(
        "/v1/runs", headers={**headers, "x-abb-workspace": auth.other.workspace_id}
    )
    error(other, 404, "WORKSPACE_NOT_FOUND")


async def test_a_role_change_or_removal_takes_effect_on_the_next_request(
    auth: Api, fake: FakeOidc
) -> None:
    from tests.auth_helpers import remove_member, set_role

    workspace = auth.tenant.context.workspace_id
    user = await add_member(auth.engine, workspace, "ada@example.com", "OWNER")
    browser = await signed_in(auth, fake)
    ws = {"x-abb-workspace": auth.tenant.workspace_id}
    assert (
        await browser.get("/v1/artifacts/art_01HZZZZZZZZZZZZZZZZZZZZZZZ/content", **ws)
    ).status_code == 404
    await set_role(auth.engine, workspace, user.id, "VIEWER")
    error(
        await browser.get("/v1/artifacts/art_01HZZZZZZZZZZZZZZZZZZZZZZZ/content", **ws),
        403,
        "PERMISSION_DENIED",
    )
    await remove_member(auth.engine, workspace, user.id)
    error(await browser.get("/v1/runs", **ws), 404, "WORKSPACE_NOT_FOUND")


async def test_a_user_denied_gets_permission_denied_not_a_scope_error(
    auth: Api, fake: FakeOidc
) -> None:
    await add_member(auth.engine, auth.tenant.context.workspace_id, "ada@example.com", "BILLING")
    browser = await signed_in(auth, fake)
    denied = await browser.get("/v1/runs", **{"x-abb-workspace": auth.tenant.workspace_id})
    body = error(denied, 403, "PERMISSION_DENIED")
    assert body["details"] == {"required_permission": "run.read"}


async def test_writes_by_cookie_need_the_web_origin(auth: Api, fake: FakeOidc) -> None:
    await add_member(auth.engine, auth.tenant.context.workspace_id, "ada@example.com", "OWNER")
    browser = await signed_in(auth, fake)
    ws = {"x-abb-workspace": auth.tenant.workspace_id}
    for origin in (None, "null", "https://evil.example"):
        headers = {**browser.cookie_header(), **ws, **({"origin": origin} if origin else {})}
        refused = await auth.client.post("/v1/events/batch", content=b"{}", headers=headers)
        error(refused, 403, "CSRF_REJECTED")
    # With the right origin the request reaches authorization: people never hold event.write.
    headers = {**browser.cookie_header(), **ws, "origin": ORIGIN}
    denied = await auth.client.post("/v1/events/batch", content=b"{}", headers=headers)
    error(denied, 403, "PERMISSION_DENIED")


# ------------------------------------------------------------------ configuration and gating


async def test_login_is_unavailable_until_oidc_is_configured(api: Api) -> None:
    refused = await api.client.get("/v1/auth/login")
    error(refused, 503, "AUTH_NOT_CONFIGURED")
    assert (await api.get("/v1/runs", token="reader")).status_code == 200  # keys are unaffected


async def test_the_login_endpoints_have_a_global_backstop(
    database_url: str, runtime_database_url: str, engine: AsyncEngine, fake: FakeOidc
) -> None:
    assert isinstance(fake.clock, Tick)
    limiter = InMemoryRateLimiter(
        events_per_second=0.001, burst_events=3, bytes_per_second=1.0, burst_bytes=1
    )
    async for instance in build_api(
        database_url,
        engine,
        settings=oidc_settings(database_url),
        clock=fake.clock,
        app_options={"oidc_transport": fake.transport(), "login_limiter": limiter},
    ):
        statuses = [(await instance.client.get("/v1/auth/login")).status_code for _ in range(5)]
        assert statuses == [302, 302, 302, 429, 429]
        limited = await instance.client.get("/v1/auth/login")
        assert limited.headers["retry-after"]
        # A callback is not behind that pool: a flood of starts fails no one's sign-in (review F1).
        assert (await instance.client.get("/v1/auth/callback")).status_code != 429
        # Keys and the rest of the API are not behind that limiter.
        assert (await instance.get("/v1/runs", token="reader")).status_code == 200


def test_unsafe_sign_in_configuration_is_refused_at_startup() -> None:
    base: dict[str, Any] = {"database_url": "postgresql+asyncpg://x:x@h/d"}
    good = {**base, "oidc_issuer": "https://idp.example", "oidc_client_id": "c",
            "web_origin": "https://app.example"}  # fmt: skip
    Settings(**good, environment="production")
    bad: list[dict[str, Any]] = [
        {**good, "environment": "production", "oidc_issuer": "http://idp.example"},
        {**good, "environment": "staging", "oidc_issuer": "http://idp.example"},
        {**good, "environment": "production", "web_origin": "http://localhost:3000"},
        {**good, "environment": "production", "web_origin": "https://app.example/path"},
        {**good, "web_origin": "https://user@app.example"},
        {**good, "oidc_client_id": None},
        {**good, "web_origin": None},
        {**good, "environment": "development", "web_origin": "http://app.example"},
    ]
    for settings in bad:
        with pytest.raises(ValidationError):
            Settings(**settings)
    # The same http values are fine for a local development setup.
    Settings(**{**good, "environment": "development", "oidc_issuer": "http://localhost:8900",
                "web_origin": "http://localhost:3000"})  # fmt: skip


def test_dev_sessions_need_both_a_local_environment_and_the_explicit_flag() -> None:
    db = "postgresql+asyncpg://x:x@h/d"
    cases = {
        ("development", False): False,
        ("development", True): True,
        ("test", True): True,
        ("staging", True): False,
        ("production", True): False,
        ("test", False): False,
    }
    for (environment, flag), expected in cases.items():
        settings = Settings(database_url=db, environment=environment, allow_dev_sessions=flag)  # type: ignore[arg-type]
        assert settings.dev_sessions_enabled is expected, (environment, flag)


async def test_the_secure_variants_use_the_host_prefix_and_secure_cookies(
    database_url: str, runtime_database_url: str, engine: AsyncEngine, fake: FakeOidc
) -> None:
    assert isinstance(fake.clock, Tick)
    origin = "https://dashboard.example.test"
    fake.redirect_uris = (f"{origin}/api/auth/callback",)
    settings = make_settings(
        database_url, oidc_issuer=ISSUER, oidc_client_id=CLIENT_ID, web_origin=origin
    )
    async for instance in build_api(
        database_url, engine, settings=settings, clock=fake.clock,
        app_options={"oidc_transport": fake.transport()},
    ):  # fmt: skip
        started = await instance.client.get("/v1/auth/login")
        instance.client.cookies.clear()
        login_cookie = cookies_set(started)["abb_login"]
        assert "Secure" in login_cookie and "Path=/api/auth" in login_cookie
        callback_url = fake.approve(started.headers["location"], "ada@example.com")
        query = {k: v[0] for k, v in parse_qs(urlsplit(callback_url).query).items()}
        done = await instance.client.get(
            "/v1/auth/callback",
            params=query,
            headers={"cookie": f"abb_login={cookie_value(login_cookie)}"},
        )
        instance.client.cookies.clear()
        session = cookies_set(done)["__Host-abb_session"]
        for attribute in ("Secure", "HttpOnly", "SameSite=Lax", "Path=/"):
            assert attribute in session
        assert "Domain" not in session
        token = cookie_value(session)
        me = await instance.client.get("/v1/me", headers={"cookie": f"__Host-abb_session={token}"})
        assert me.status_code == 200
        # The plain name is not accepted when the secure one is in force.
        plain = await instance.client.get("/v1/me", headers={"cookie": f"abb_session={token}"})
        assert plain.status_code == 401


async def test_the_startup_warns_when_sign_in_is_configured_outside_production(
    auth: Api, capsys: pytest.CaptureFixture[str], database_url: str, fake: FakeOidc
) -> None:
    from abb_api.main import create_app

    # create_app installs its own JSON log handler, so read what it printed.
    create_app(oidc_settings(database_url), oidc_transport=fake.transport())
    assert "ABB_ENVIRONMENT is not production" in capsys.readouterr().out


# ------------------------------------------------------------------ log hygiene


async def test_the_login_flow_logs_no_secret_token_email_or_identifier_of_the_flow(
    auth: Api, fake: FakeOidc, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    secrets_seen: list[str] = []
    for setup in (None, "bad_nonce", "bad_signature", "email_verified"):
        if setup:
            fake.knobs.bad_nonce = setup == "bad_nonce"
            fake.knobs.bad_signature = setup == "bad_signature"
            fake.knobs.email_verified = False if setup == "email_verified" else True
        browser = Browser(auth, fake)
        started = await browser.start("/w/acme/projects/all")
        query = parse_qs(urlsplit(started.headers["location"]).query)
        url = fake.approve(started.headers["location"], "secret.person@example.com")
        callback_query = parse_qs(urlsplit(url).query)
        await browser.callback(url)
        secrets_seen += [
            query["state"][0], query["nonce"][0], query["code_challenge"][0],
            callback_query["code"][0], "secret.person@example.com", "secret.person",
        ]  # fmt: skip
        if browser.session:
            secrets_seen.append(browser.session)
    await auth.client.get("/v1/me", headers={"cookie": f"{SESSION_COOKIE}=garbage"})
    rendered = "\n".join(
        r.getMessage() + " " + " ".join(str(v) for k, v in r.__dict__.items() if k != "args")
        for r in caplog.records
    )
    for secret in secrets_seen:
        assert secret not in rendered, secret[:6]
    assert "Bearer" not in rendered and "id_token" not in rendered


async def test_minted_sessions_work_without_an_identity_provider(auth: Api) -> None:
    """The matrix tests rely on this: a session is a database row plus a cookie."""
    user = await add_member(
        auth.engine, auth.tenant.context.workspace_id, "dev@example.com", "ADMIN"
    )
    token = await mint_session(auth.engine, user.id, auth.clock())
    headers = {"cookie": f"{SESSION_COOKIE}={token}", "x-abb-workspace": auth.tenant.workspace_id}
    assert (await auth.client.get("/v1/runs", headers=headers)).status_code == 200
