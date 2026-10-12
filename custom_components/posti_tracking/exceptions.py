"""Errors from Posti's services."""


class PostiError(Exception):
    """Posti couldn't be reached, or it answered with something unexpected."""


class PostiAuthError(PostiError):
    """Posti didn't accept the login, or the integration's device is no longer trusted."""


class InvalidReturnAddress(PostiError):
    """The address pasted after the strong authentication isn't the one Posti returns to."""
