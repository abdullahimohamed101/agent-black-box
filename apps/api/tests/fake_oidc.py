"""A small in-process OpenID Connect provider for development and tests (ADR-060, D5).

It is never mounted in `create_app`. Tests reach it through `httpx.ASGITransport`;
`scripts/fake-oidc.sh` serves it on a port for `make dev`, compose and the Playwright login spec.
The authorize page accepts any email, so no account exists anywhere. `Knobs` make it misbehave in
each way a login must survive.

    python -m tests.fake_oidc --port 8900 --client-id abb-dev \
        --redirect-uri http://localhost:3000/api/auth/callback
"""

import argparse
import base64
import hashlib
import hmac
import html
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

_KEYS: list[rsa.RSAPrivateKey] = []


def _key(index: int) -> rsa.RSAPrivateKey:
    """Process-wide RSA keys: generating them is the slow part of every test otherwise."""
    while len(_KEYS) <= index:
        _KEYS.append(rsa.generate_private_key(public_exponent=65537, key_size=2048))
    return _KEYS[index]


@dataclass
class Knobs:
    """Set any of these to make the next ID tokens bad in one specific way."""

    bad_nonce: bool = False
    bad_signature: bool = False  # signed by a key the JWKS does not list, under a listed kid
    audience: str | None = None  # override `aud`
    authorized_party: str | None = None  # add `azp`
    issuer_claim: str | None = None  # override `iss` in the token
    discovery_issuer: str | None = None  # override `issuer` in the discovery document
    discovery_extra: dict[str, Any] = field(default_factory=dict)  # e.g. a foreign token_endpoint
    email_verified: bool | str | None = True  # None omits the claim; a str is sent as is
    expired: bool = False
    unknown_kid: bool = False  # sign with a key whose kid is not in the JWKS
    omit_email: bool = False
    algorithm: str = "RS256"  # "HS256" makes a token signed with the public key as a secret


class FakeOidc:
    def __init__(
        self,
        *,
        issuer: str = "http://idp.test",
        client_id: str = "abb-test",
        redirect_uris: tuple[str, ...] = (),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.issuer = issuer.rstrip("/")
        self.client_id = client_id
        self.redirect_uris = redirect_uris
        self.clock = clock or (lambda: datetime.now(UTC))
        self.knobs = Knobs()
        self.key_index = 0  # which process-wide key is "current"; rotate() moves it
        self.published = [0]  # key indexes listed in the JWKS
        self._codes: dict[str, dict[str, Any]] = {}
        self.requests: list[str] = []  # paths asked of the provider, for assertions
        self.app = Starlette(
            routes=[
                Route("/.well-known/openid-configuration", self._discovery),
                Route("/authorize", self._authorize, methods=["GET", "POST"]),
                Route("/token", self._token, methods=["POST"]),
                Route("/jwks", self._jwks),
                Route("/userinfo", self._userinfo),
            ]
        )

    # ------------------------------------------------------------------ test helpers

    def transport(self) -> httpx.ASGITransport:
        return httpx.ASGITransport(app=self.app)

    def rotate(self, *, keep_old: bool = False) -> None:
        """Start signing with a new key; publish it (and the old one too when `keep_old`)."""
        self.key_index += 1
        self.published = [*self.published, self.key_index] if keep_old else [self.key_index]

    def approve(
        self,
        authorize_url: str,
        email: str,
        *,
        subject: str | None = None,
        name: str | None = None,
    ) -> str:
        """What the person does at the provider: sign in as `email`. Returns the callback URL."""
        query = dict(_parse_query(authorize_url))
        return self._issue_code(query, email, subject, name)

    # ------------------------------------------------------------------ endpoints

    def _kid(self, index: int) -> str:
        return f"fake-key-{index}"

    async def _discovery(self, request: Request) -> Response:
        self.requests.append(request.url.path)
        document: dict[str, Any] = {
            "issuer": self.knobs.discovery_issuer or self.issuer,
            "authorization_endpoint": f"{self.issuer}/authorize",
            "token_endpoint": f"{self.issuer}/token",
            "jwks_uri": f"{self.issuer}/jwks",
            "userinfo_endpoint": f"{self.issuer}/userinfo",
            "response_types_supported": ["code"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "code_challenge_methods_supported": ["S256"],
            **self.knobs.discovery_extra,
        }
        return JSONResponse(document)

    async def _jwks(self, request: Request) -> Response:
        self.requests.append(request.url.path)
        keys = []
        for index in self.published:
            jwk = RSAAlgorithm.to_jwk(_key(index).public_key(), as_dict=True)
            keys.append({**jwk, "kid": self._kid(index), "use": "sig", "alg": "RS256"})
        return JSONResponse({"keys": keys})

    async def _authorize(self, request: Request) -> Response:
        if request.method == "GET":
            query = dict(request.query_params)
            if self.redirect_uris and query.get("redirect_uri") not in self.redirect_uris:
                return Response("unregistered redirect_uri", status_code=400)
            hidden = "".join(
                f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">'
                for k, v in query.items()
            )
            return HTMLResponse(
                "<!doctype html><title>Fake identity provider</title>"
                "<h1>Fake identity provider</h1><p>Development only. Any email signs in.</p>"
                f'<form method="post">{hidden}'
                '<label>Email <input name="email" type="email" required autofocus></label> '
                '<button type="submit">Sign in</button></form>'
            )
        form = await _form(request)
        email = form.pop("email", "")
        if self.redirect_uris and form.get("redirect_uri") not in self.redirect_uris:
            return Response("unregistered redirect_uri", status_code=400)
        return RedirectResponse(self._issue_code(form, email, None, None), status_code=302)

    def _issue_code(
        self, query: dict[str, str], email: str, subject: str | None, name: str | None
    ) -> str:
        code = secrets.token_urlsafe(24)
        self._codes[code] = {
            "email": email,
            "subject": subject or "sub-" + hashlib.sha1(email.lower().encode()).hexdigest()[:12],  # noqa: S324
            "name": name,
            "nonce": query.get("nonce"),
            "challenge": query.get("code_challenge"),
            "redirect_uri": query.get("redirect_uri", ""),
            "client_id": query.get("client_id"),
        }
        params = {"code": code, "state": query.get("state", "")}
        return f"{query.get('redirect_uri', '')}?{urlencode(params)}"

    async def _token(self, request: Request) -> Response:
        self.requests.append(request.url.path)
        form = await _form(request)
        grant = self._codes.pop(form.get("code", ""), None)  # single use
        if grant is None or form.get("grant_type") != "authorization_code":
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        verifier = form.get("code_verifier", "")
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        if not hmac.compare_digest(challenge.rstrip(b"=").decode(), grant["challenge"] or ""):
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        if (
            form.get("redirect_uri") != grant["redirect_uri"]
            or form.get("client_id") != grant["client_id"]
        ):
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        return JSONResponse(
            {
                "id_token": self._id_token(grant),
                "access_token": secrets.token_urlsafe(16),
                "token_type": "Bearer",
                "expires_in": 300,
            }
        )

    async def _userinfo(self, request: Request) -> Response:
        return JSONResponse({"error": "not implemented in the fake"}, status_code=501)

    def _id_token(self, grant: dict[str, Any]) -> str:
        k, now = self.knobs, self.clock()
        claims: dict[str, Any] = {
            "iss": k.issuer_claim or self.issuer,
            "sub": grant["subject"],
            "aud": k.audience or self.client_id,
            "iat": int((now - timedelta(seconds=5)).timestamp()),
            "exp": int(
                (now + (timedelta(minutes=-10) if k.expired else timedelta(minutes=5))).timestamp()
            ),
            "nonce": "wrong-nonce" if k.bad_nonce else grant["nonce"],
        }
        if not k.omit_email:
            claims["email"] = grant["email"]
        if k.email_verified is not None:
            claims["email_verified"] = k.email_verified
        if grant["name"]:
            claims["name"] = grant["name"]
        if k.authorized_party:
            claims["azp"] = k.authorized_party
        if k.algorithm == "HS256":  # algorithm confusion: the public key used as an HMAC secret
            public = RSAAlgorithm.to_jwk(_key(self.key_index).public_key())
            return _hs256(claims, public, self._kid(self.key_index))
        if k.unknown_kid:
            return jwt.encode(claims, _key(7), "RS256", headers={"kid": "not-published"})
        signing = _key(9) if k.bad_signature else _key(self.key_index)
        return jwt.encode(claims, signing, "RS256", headers={"kid": self._kid(self.key_index)})


async def _form(request: Request) -> dict[str, str]:
    """An urlencoded body, parsed by hand: python-multipart is not a dependency."""
    return dict(parse_qsl((await request.body()).decode(), keep_blank_values=True))


def _hs256(claims: dict[str, Any], secret: str, kid: str) -> str:
    """PyJWT refuses to HMAC with a PEM/JWK key; build the token by hand to simulate the attack."""
    import json

    def b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    header = b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": kid}).encode())
    payload = b64(json.dumps(claims).encode())
    signature = hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    return f"{header}.{payload}.{b64(signature)}"


def _parse_query(url: str) -> list[tuple[str, str]]:
    return parse_qsl(urlsplit(url).query, keep_blank_values=True)


def main() -> None:  # pragma: no cover - exercised by scripts/fake-oidc.sh
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8900)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--issuer", default=None)
    parser.add_argument("--client-id", default="abb-dev")
    parser.add_argument("--redirect-uri", action="append", default=[])
    args = parser.parse_args()
    issuer = args.issuer or f"http://localhost:{args.port}"
    provider = FakeOidc(
        issuer=issuer, client_id=args.client_id, redirect_uris=tuple(args.redirect_uri)
    )
    uvicorn.run(provider.app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
