"""Mediasoup HTTP API exceptions."""


class MediasoupApiError(Exception):
    """Raised when the mediasoup HTTP API returns an error response."""

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        self.message = message
        super().__init__(f'Mediasoup API error {status}: {message}')
