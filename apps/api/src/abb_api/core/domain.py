"""Domain errors raised by repositories and services. HTTP mapping happens at the edge."""


class DomainError(Exception):
    """Base class; carries a safe, human-readable message."""


class AlreadyExistsError(DomainError):
    """A unique business key (for example a slug) is already taken."""


class NotFoundError(DomainError):
    """The entity does not exist *for this tenant*. Never reveals other tenants' rows."""
