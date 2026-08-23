class MantleError(Exception):
    """An expected, user-actionable Mantle failure."""


class NotFoundError(MantleError):
    """A requested Mantle resource does not exist."""


class ConflictError(MantleError):
    """An operation conflicts with existing Mantle state."""

