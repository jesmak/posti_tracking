"""Errors from Posti's services."""


class PostiError(Exception):
    """Posti couldn't be reached, or it answered with something unexpected."""


class PostiAuthError(PostiError):
    """Posti didn't accept the user name and password."""
