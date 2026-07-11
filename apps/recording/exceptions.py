class RecordingError(Exception):
    """Base error for recording operations."""


class RecordingAlreadyActiveError(RecordingError):
    """Raised when start is requested while a recording is in progress."""


class RecordingNotActiveError(RecordingError):
    """Raised when stop is requested with no active recording."""


class IngestManagerNotRunningError(RecordingError):
    """Raised when the compositor ingest pipeline is not running."""
