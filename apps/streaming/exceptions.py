class StreamingError(Exception):
    """Base error for streaming operations."""


class StreamAlreadyActiveError(StreamingError):
    """Raised when start is requested while a stream is live."""


class StreamNotActiveError(StreamingError):
    """Raised when stop is requested with no active stream."""


class IngestManagerNotRunningError(StreamingError):
    """Raised when the compositor ingest pipeline is not running."""


class InvalidDestinationError(StreamingError):
    """Raised when a destination URL or type is invalid."""
