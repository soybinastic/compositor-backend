class BackgroundMusicError(Exception):
    """Base error for background music operations."""


class IngestManagerNotRunningError(BackgroundMusicError):
    """Raised when the compositor ingest pipeline is not running."""


class SceneNotActiveError(BackgroundMusicError):
    """Raised when transport is requested for a non-active scene."""
