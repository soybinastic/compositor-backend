from abc import ABC, abstractmethod

from apps.sessions.models import StudioSession


class IPipelineManager(ABC):
    """Port for GStreamer pipeline lifecycle (Dependency Inversion)."""

    @abstractmethod
    def start(self, session_id: str) -> None: ...

    @abstractmethod
    def stop(self, session_id: str) -> None: ...


class IRecorder(ABC):
    """Port for session recording control."""

    @abstractmethod
    def start_recording(self, session_id: str) -> str: ...

    @abstractmethod
    def stop_recording(self, session_id: str) -> str: ...


class IStreamer(ABC):
    """Port for live streaming egress control."""

    @abstractmethod
    def start_stream(self, session_id: str, destination_url: str) -> str: ...

    @abstractmethod
    def stop_stream(self, session_id: str) -> str: ...


class IMediaPlaneBootstrap(ABC):
    """Port for mediasoup room + compositor peer setup (Phase 2)."""

    @abstractmethod
    def bootstrap(self, session: StudioSession) -> StudioSession: ...

    @abstractmethod
    def teardown(self, session: StudioSession) -> None: ...
