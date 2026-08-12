class InvalidRtmpUrlError(Exception):
    """Raised when an RTMP ingest URL is invalid."""


class RtmpSourceNotFoundError(Exception):
    """Raised when an RTMP source does not exist for the session."""


class IngestManagerNotRunningError(Exception):
    """Raised when compositor ingest is not running for the session."""


class SourceNotFoundError(Exception):
    """Raised when a session Source does not exist."""


class SourceAlreadyAttachedError(Exception):
    """Raised when attaching a Source that is already on the scene."""


class UnsupportedSourceTypeError(Exception):
    """Raised when creating an unknown Source type."""


class SourceTypeNotImplementedError(Exception):
    """Raised when a Source type is registered but not implemented."""
