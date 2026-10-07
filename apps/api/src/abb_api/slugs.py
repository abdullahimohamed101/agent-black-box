import re

_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")


def validate_slug(value: str, what: str = "slug") -> str:
    if not _SLUG.fullmatch(value):
        raise ValueError(f"{what} must be 1-63 characters: lowercase letters, digits and hyphens")
    return value
