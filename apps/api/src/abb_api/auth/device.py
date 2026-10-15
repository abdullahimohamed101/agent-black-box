"""A coarse, safe label for a browser, shown on the sessions page (KI-068).

The `User-Agent` header is attacker-controlled text. It is never stored or shown: it is read only
up to a fixed length and reduced to a name from two closed vocabularies, so the stored hint is one
of a few dozen fixed strings and can carry no markup, control character or identifier.
"""

import re

MAX_AGENT_CHARS = 512  # longer headers are cut before any matching (bounded work)

# Order matters: the first match wins (Edge and Opera also say Chrome; Chrome also says Safari).
_BROWSERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Edge", re.compile(r"\b(?:Edg|EdgA|EdgiOS|Edge)/")),
    ("Opera", re.compile(r"\b(?:OPR|Opera)/")),
    ("Firefox", re.compile(r"\b(?:Firefox|FxiOS)/")),
    ("Chrome", re.compile(r"\b(?:Chrome|CriOS|Chromium)/")),
    ("Safari", re.compile(r"\bSafari/")),
)
_SYSTEMS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Android", re.compile(r"\bAndroid\b")),
    ("iOS", re.compile(r"\b(?:iPhone|iPad|iPod)\b")),
    ("Windows", re.compile(r"\bWindows\b")),
    ("macOS", re.compile(r"\bMacintosh\b|\bMac OS X\b")),
    ("ChromeOS", re.compile(r"\bCrOS\b")),
    ("Linux", re.compile(r"\bLinux\b|\bX11\b")),
)


def device_hint(user_agent: str | None) -> str | None:
    """`"Firefox on Linux"`, `"Chrome"`, `"macOS"`, or None when nothing is recognised."""
    if not user_agent:
        return None
    text = user_agent[:MAX_AGENT_CHARS]
    browser = next((name for name, rx in _BROWSERS if rx.search(text)), None)
    system = next((name for name, rx in _SYSTEMS if rx.search(text)), None)
    if browser and system:
        return f"{browser} on {system}"
    return browser or system
