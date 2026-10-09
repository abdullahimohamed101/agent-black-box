"""OpenID Connect relying party: authorization code + PKCE against a discovery document (ADR-060).

Everything the provider tells us is hostile until checked: the discovery document must name the
configured issuer and https endpoints on hosts we expect, the ID token must be signed by a key from
the provider's JWKS with an explicitly allowed algorithm, and issuer, audience, authorized party,
nonce and times are verified here. Time comes from the injected clock, so tests control it.

Logging rule: only the issuer and key id are ever logged on failure; never a code, state, token,
secret, email, or anything the provider wrote in an error description.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
import jwt
from jwt import PyJWK, PyJWKSet

from abb_api.clock import Clock

logger = logging.getLogger(__name__)

ALLOWED_ALGORITHMS = ("RS256", "ES256")
DISCOVERY_TTL = timedelta(hours=1)
JWKS_TTL = timedelta(hours=1)
JWKS_REFETCH_INTERVAL = timedelta(seconds=60)
CLOCK_SKEW = timedelta(seconds=60)
HTTP_TIMEOUT = httpx.Timeout(5.0)
MAX_RESPONSE_BYTES = 1_000_000


class OidcError(Exception):
    """A login that must fail. `reason` is a fixed code that is safe to log."""

    def __init__(self, reason: str, *, status: int = 400, kid: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status
        self.kid = kid


class OidcUnavailable(Exception):
    """The provider could not be reached or answered nonsense: retry later (503)."""


@dataclass(frozen=True)
class Discovery:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str


@dataclass(frozen=True)
class Identity:
    subject: str
    email: str
    email_verified: bool
    name: str | None


def new_pkce() -> tuple[str, str]:
    """(verifier, S256 challenge)."""
    verifier = secrets.token_urlsafe(64)  # 86 characters, within RFC 7636's 43..128
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge.rstrip(b"=").decode("ascii")


def _effective_port(scheme: str, port: int | None) -> int:
    return port if port is not None else (443 if scheme == "https" else 80)


class OidcClient:
    def __init__(
        self,
        *,
        issuer: str,
        client_id: str,
        client_secret: str | None,
        redirect_uri: str,
        clock: Clock,
        extra_hosts: frozenset[str] = frozenset(),
        allow_http: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.issuer = issuer
        self.client_id = client_id
        self._secret = client_secret
        self.redirect_uri = redirect_uri
        self._clock = clock
        self._extra_hosts = extra_hosts
        self._allow_http = allow_http
        self._transport = transport
        self._lock = asyncio.Lock()
        self._discovery: tuple[Discovery, datetime] | None = None
        self._jwks: tuple[PyJWKSet, datetime] | None = None
        self._jwks_last_fetch: datetime | None = None

    # ------------------------------------------------------------------ HTTP

    async def _request(
        self, method: str, url: str, *, data: Mapping[str, str] | None = None
    ) -> dict[str, Any]:
        # trust_env=False: no proxy from the environment sees the client secret or the code.
        # follow_redirects=False: a provider (or whoever sits in front of it) cannot bounce us.
        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                trust_env=False,
                follow_redirects=False,
                timeout=HTTP_TIMEOUT,
            ) as client:
                async with client.stream(
                    method, url, data=data, headers={"accept": "application/json"}
                ) as response:
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body += chunk
                        if len(body) > MAX_RESPONSE_BYTES:
                            raise OidcUnavailable("response too large")
                    status = response.status_code
        except (httpx.HTTPError, OSError) as exc:
            logger.warning(
                "identity provider unreachable", extra={"error_type": type(exc).__name__}
            )
            raise OidcUnavailable("unreachable") from None
        if status != 200:
            logger.warning(
                "identity provider answered an error", extra={"status": status, "iss": self.issuer}
            )
            if method == "POST" and 400 <= status < 500:
                raise OidcError("token_exchange_rejected", status=401)
            raise OidcUnavailable(f"status {status}")
        try:
            document = json.loads(bytes(body))
        except ValueError:
            raise OidcUnavailable("not json") from None
        if not isinstance(document, dict):
            raise OidcUnavailable("not an object")
        return document

    # ------------------------------------------------------------------ discovery

    def _check_endpoint(
        self, url: object, issuer_host: str, issuer_port: int, issuer_scheme: str
    ) -> str:
        if not isinstance(url, str):
            raise OidcError("discovery_invalid")
        parts = urlsplit(url)
        https = parts.scheme == "https"
        http_ok = parts.scheme == "http" and self._allow_http and issuer_scheme == "http"
        if not (https or http_ok) or not parts.hostname or parts.username or parts.fragment:
            raise OidcError("discovery_invalid")
        same_origin = (
            parts.hostname == issuer_host
            and _effective_port(parts.scheme, parts.port) == issuer_port
        )
        if not same_origin and parts.hostname not in self._extra_hosts:
            raise OidcError("discovery_untrusted_host")
        return url

    async def discovery(self) -> Discovery:
        now = self._clock()
        if self._discovery and now - self._discovery[1] < DISCOVERY_TTL:
            return self._discovery[0]
        document = await self._request(
            "GET", self.issuer.rstrip("/") + "/.well-known/openid-configuration"
        )
        if document.get("issuer") != self.issuer:  # OIDC Discovery §4.3: exact match
            logger.warning("discovery issuer mismatch", extra={"iss": self.issuer})
            raise OidcError("discovery_issuer_mismatch")
        parts = urlsplit(self.issuer)
        host, port = parts.hostname or "", _effective_port(parts.scheme, parts.port)
        methods = document.get("code_challenge_methods_supported")
        if methods is not None and "S256" not in methods:
            raise OidcError("discovery_no_pkce")
        found = Discovery(
            issuer=self.issuer,
            authorization_endpoint=self._check_endpoint(
                document.get("authorization_endpoint"), host, port, parts.scheme
            ),
            token_endpoint=self._check_endpoint(
                document.get("token_endpoint"), host, port, parts.scheme
            ),
            jwks_uri=self._check_endpoint(document.get("jwks_uri"), host, port, parts.scheme),
        )
        self._discovery = (found, now)
        return found

    async def authorize_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        endpoint = (await self.discovery()).authorization_endpoint
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.client_id,
                "redirect_uri": self.redirect_uri,
                "scope": "openid email profile",
                "state": state,
                "nonce": nonce,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{endpoint}{'&' if '?' in endpoint else '?'}{query}"

    # ------------------------------------------------------------------ token

    async def exchange_code(self, code: str, code_verifier: str) -> str:
        """Trade the authorization code for an ID token (back channel)."""
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "client_id": self.client_id,
            "code_verifier": code_verifier,
        }
        if self._secret:
            form["client_secret"] = self._secret
        document = await self._request("POST", (await self.discovery()).token_endpoint, data=form)
        id_token = document.get("id_token")
        if not isinstance(id_token, str) or not id_token:
            raise OidcError("no_id_token", status=401)
        return id_token

    async def _key(self, kid: str | None, alg: str) -> PyJWK:
        """The provider's key for this token, refetching an unknown `kid` at most once a minute."""
        async with self._lock:
            now = self._clock()
            if self._jwks is None or now - self._jwks[1] >= JWKS_TTL:
                await self._fetch_jwks(now)
            key = self._find_key(kid, alg)
            if key is None and (
                self._jwks_last_fetch is None
                or now - self._jwks_last_fetch >= JWKS_REFETCH_INTERVAL
            ):
                await self._fetch_jwks(now)  # a rotation: look once more, at most once a minute
                key = self._find_key(kid, alg)
        if key is None:
            raise OidcError("unknown_kid", status=401, kid=kid)
        return key

    async def _fetch_jwks(self, now: datetime) -> None:
        document = await self._request("GET", (await self.discovery()).jwks_uri)
        try:
            self._jwks = (PyJWKSet.from_dict(document), now)
        except jwt.PyJWTError:
            raise OidcUnavailable("jwks unusable") from None
        self._jwks_last_fetch = now

    def _find_key(self, kid: str | None, alg: str) -> PyJWK | None:
        if self._jwks is None:
            return None
        candidates = [
            k
            for k in self._jwks[0].keys
            if k.public_key_use in (None, "sig") and k.algorithm_name == alg
        ]
        if kid is not None:
            return next((k for k in candidates if k.key_id == kid), None)
        # No `kid` in the header: acceptable only when the provider has exactly one candidate.
        return candidates[0] if len(candidates) == 1 else None

    async def verify_id_token(self, id_token: str, *, nonce: str) -> Identity:
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError:
            raise OidcError("token_malformed", status=401) from None
        alg, kid = header.get("alg"), header.get("kid")
        if alg not in ALLOWED_ALGORITHMS:  # never `none`, never HS*; jku/jwk/x5u are ignored
            raise OidcError("token_algorithm", status=401)
        if kid is not None and not isinstance(kid, str):
            raise OidcError("token_malformed", status=401)
        key = await self._key(kid, alg)
        try:
            claims = jwt.decode(
                id_token,
                key.key,
                algorithms=[alg],
                audience=self.client_id,
                issuer=self.issuer,
                # Times are checked below against the injected clock.
                options={
                    "verify_exp": False,
                    "verify_nbf": False,
                    "verify_iat": False,
                    "require": ["exp", "iss", "aud", "sub"],
                },
            )
        except jwt.InvalidSignatureError:
            raise OidcError("token_signature", status=401, kid=kid) from None
        except jwt.PyJWTError:
            raise OidcError("token_invalid", status=401, kid=kid) from None
        self._check_times(claims, kid)
        audience = claims.get("aud")
        authorized_party = claims.get("azp")
        if authorized_party is not None and authorized_party != self.client_id:
            raise OidcError("token_azp", status=401, kid=kid)
        if isinstance(audience, list) and len(audience) > 1 and authorized_party is None:
            raise OidcError("token_azp", status=401, kid=kid)
        token_nonce = claims.get("nonce")
        if not isinstance(token_nonce, str) or not hmac.compare_digest(
            token_nonce.encode(), nonce.encode()
        ):
            raise OidcError("token_nonce", status=401, kid=kid)
        return self._identity(claims, kid)

    def _check_times(self, claims: Mapping[str, Any], kid: str | None) -> None:
        now = self._clock().timestamp()
        skew = CLOCK_SKEW.total_seconds()
        exp, nbf, iat = claims.get("exp"), claims.get("nbf"), claims.get("iat")
        if not isinstance(exp, (int, float)) or now - skew >= exp:
            raise OidcError("token_expired", status=401, kid=kid)
        for value in (nbf, iat):
            if value is not None and (not isinstance(value, (int, float)) or value > now + skew):
                raise OidcError("token_not_yet_valid", status=401, kid=kid)

    @staticmethod
    def _identity(claims: Mapping[str, Any], kid: str | None) -> Identity:
        subject, email = claims.get("sub"), claims.get("email")
        if not isinstance(subject, str) or not 0 < len(subject) <= 255:
            raise OidcError("token_subject", status=401, kid=kid)
        if not isinstance(email, str) or not 3 <= len(email) <= 320 or "@" not in email:
            raise OidcError("token_email_missing", status=401, kid=kid)
        verified = claims.get("email_verified")
        if verified is None:
            raise OidcError("email_verified_missing", status=401, kid=kid)
        name = claims.get("name")
        return Identity(
            subject=subject,
            email=email.strip().lower(),
            email_verified=verified is True,  # a string "true" is not accepted: fail closed
            name=name[:200] if isinstance(name, str) else None,
        )
