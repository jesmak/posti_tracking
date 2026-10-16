"""Errors from Posti's services."""


class PostiError(Exception):
    """Posti couldn't be reached, or it answered with something unexpected."""


class PostiAuthError(PostiError):
    """Posti didn't accept the login, or the integration's device is no longer trusted."""


class PostiDeviceGone(PostiAuthError):
    """The integration's device has been removed from the account's trusted devices, so there is nothing to deactivate."""


class PostiNotJson(PostiError):
    """Posti answered with something other than JSON, such as an empty or HTML page."""

    def __init__(self, status: int) -> None:
        super().__init__(f"Posti answered with something other than JSON (status {status})")
        self.status = status


class InvalidReturnAddress(PostiError):
    """The address pasted after the strong authentication isn't the one Posti returns to."""
