"""Graphics domain exceptions."""


class GraphicsError(Exception):
    """Base exception for graphics operations."""


class IngestManagerNotRunningError(GraphicsError):
    """Raised when graphics require a running compositor ingest manager."""
