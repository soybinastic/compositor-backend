class InvalidRtmpUrlError(Exception):
    """Raised when an RTMP ingest URL is invalid."""


class RtmpSourceNotFoundError(Exception):
    """Raised when an RTMP source does not exist for the session."""


class IngestManagerNotRunningError(Exception):
    """Raised when compositor ingest is not running for the session."""
