"""The client address, only when a proxy we run says so (ADR-153).

Without `ABB_TRUST_PROXY` there is no client address at all: the header is client-controlled, and
using it would let a caller pick its own bucket (evade) or name someone else's (frame). With it, the
last `X-Forwarded-For` entry is the one the trusted proxy appended; earlier entries are whatever the
client sent. The same rule as the web server's sign-in limiter.
"""

import hashlib
import ipaddress

from fastapi import Request


def client_address(request: Request, *, trust_proxy: bool) -> str | None:
    """A normalised address (IPv6 collapsed to its /64, the smallest block one customer holds)."""
    if not trust_proxy:
        return None
    header = request.headers.get("x-forwarded-for")
    if not header:
        return None
    last = header.rsplit(",", 1)[-1].strip()
    if not last or len(last) > 64:
        return None
    try:
        parsed = ipaddress.ip_address(last)
    except ValueError:
        return None
    if isinstance(parsed, ipaddress.IPv6Address):
        if parsed.ipv4_mapped is not None:
            return str(parsed.ipv4_mapped)
        return str(ipaddress.ip_network(f"{parsed}/64", strict=False).network_address)
    return str(parsed)


def address_counter_key(address: str) -> str:
    """What is stored in the counter table instead of the address: a domain-separated digest (the
    fixed prefix keeps it from colliding with any other SHA-256 use; it is not a secret salt), so a
    database reader sees a pseudonym that expires within two minutes, never the address itself.
    An IPv4 space can be enumerated, so this is a pseudonym, not anonymisation (ADR-153 point 5)."""
    return hashlib.sha256(b"abb-auth-fail-address:" + address.encode("ascii")).hexdigest()[:32]
