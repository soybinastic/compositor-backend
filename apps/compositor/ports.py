"""UDP port allocation for RTP ingest branches."""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass

from django.conf import settings


@dataclass(frozen=True)
class PortPair:
    rtp_port: int
    rtcp_port: int


@dataclass(frozen=True)
class ParticipantPorts:
    participant_peer_id: str
    audio: PortPair
    video: PortPair


class PortAllocator:
    """
    Allocates non-overlapping RTP/RTCP port pairs per participant.

    Each participant needs two pairs: audio (rtp+rtcp) and video (rtp+rtcp).
    """

    def __init__(
        self,
        min_port: int | None = None,
        max_port: int | None = None,
    ) -> None:
        self._min_port = min_port or settings.COMPOSITOR_RTP_PORT_MIN
        self._max_port = max_port or settings.COMPOSITOR_RTP_PORT_MAX
        self._next_port = self._min_port
        self._allocated: set[int] = set()
        self._free_pairs: deque[PortPair] = deque()
        self._lock = threading.Lock()

    def allocate_participant_ports(self, participant_peer_id: str) -> ParticipantPorts:
        with self._lock:
            audio = self._allocate_pair()
            video = self._allocate_pair()
            return ParticipantPorts(
                participant_peer_id=participant_peer_id,
                audio=audio,
                video=video,
            )

    def release_participant_ports(self, ports: ParticipantPorts) -> None:
        with self._lock:
            for pair in (ports.audio, ports.video):
                self._allocated.discard(pair.rtp_port)
                self._allocated.discard(pair.rtcp_port)
                self._free_pairs.append(pair)

    def _allocate_pair(self) -> PortPair:
        if self._free_pairs:
            pair = self._free_pairs.popleft()
            self._allocated.add(pair.rtp_port)
            self._allocated.add(pair.rtcp_port)
            return pair

        while self._next_port + 1 <= self._max_port:
            rtp_port = self._next_port
            rtcp_port = self._next_port + 1
            self._next_port += 2

            if rtp_port not in self._allocated and rtcp_port not in self._allocated:
                self._allocated.add(rtp_port)
                self._allocated.add(rtcp_port)
                return PortPair(rtp_port=rtp_port, rtcp_port=rtcp_port)

        raise RuntimeError('No free RTP ports available for compositor ingest')
