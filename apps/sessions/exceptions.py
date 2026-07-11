"""Session domain exceptions."""


class SessionError(Exception):
    """Base exception for session operations."""


class SessionNotFoundError(SessionError):
    """Raised when a session does not exist."""


class SessionEndedError(SessionError):
    """Raised when an operation targets an ended session."""


class InvalidInviteTokenError(SessionError):
    """Raised when an invite token does not match the session."""
