"""SFU PlainTransport produce path for compositor-owned URI video sources.

Flow (mediasoup BroadcasterPeer producer direction):
1. create_plain_transport(direction='producer') → mediasoup listen ip/port
2. create_producer(kind, rtpParameters, appData={source, sourceId})
3. GStreamer encodes and sends RTP/RTCP via udpsink to the transport tuple

This module currently creates the mediasoup Producer and logs RTP targets.
Wiring a live GStreamer tee → x264enc/opus → rtp*pay → udpsink without
disturbing the program mix is intentionally left as a follow-up (TODO).
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import Any

from integrations.mediasoup.client import MediasoupHttpClient

logger = logging.getLogger(__name__)

_VIDEO_SSRC_BASE = 200_000
_AUDIO_SSRC_BASE = 100_000


def _default_video_rtp_parameters(*, payload_type: int = 101, ssrc: int) -> dict[str, Any]:
    return {
        'codecs': [
            {
                'mimeType': 'video/H264',
                'payloadType': payload_type,
                'clockRate': 90000,
                'parameters': {
                    'packetization-mode': 1,
                    'profile-level-id': '42e01f',
                    'level-asymmetry-allowed': 1,
                },
                'rtcpFeedback': [
                    {'type': 'nack'},
                    {'type': 'nack', 'parameter': 'pli'},
                    {'type': 'ccm', 'parameter': 'fir'},
                ],
            }
        ],
        'encodings': [{'ssrc': ssrc}],
    }


def _default_audio_rtp_parameters(*, payload_type: int = 100, ssrc: int) -> dict[str, Any]:
    return {
        'codecs': [
            {
                'mimeType': 'audio/opus',
                'payloadType': payload_type,
                'clockRate': 48000,
                'channels': 2,
                'parameters': {'sprop-stereo': 1},
            }
        ],
        'encodings': [{'ssrc': ssrc}],
    }


@dataclass
class SfuSourceEgress:
    """Registers compositor URI media as SFU producers (RTP send stub)."""

    client: MediasoupHttpClient
    room_id: str
    compositor_peer_id: str
    source_id: str
    include_audio: bool = True
    _video_transport: dict[str, Any] | None = field(default=None, init=False, repr=False)
    _audio_transport: dict[str, Any] | None = field(default=None, init=False, repr=False)
    _video_producer_id: str | None = field(default=None, init=False, repr=False)
    _audio_producer_id: str | None = field(default=None, init=False, repr=False)
    _started: bool = field(default=False, init=False, repr=False)

    @property
    def started(self) -> bool:
        return self._started

    @property
    def video_producer_id(self) -> str | None:
        return self._video_producer_id

    @property
    def audio_producer_id(self) -> str | None:
        return self._audio_producer_id

    def start(self) -> None:
        if self._started:
            return

        # Produce requires a joined BroadcasterPeer (may already be joined via ingest).
        try:
            self.client.join_broadcaster(self.room_id, self.compositor_peer_id)
        except Exception as exc:
            # Idempotent join: ignore "already joined" style failures from mediasoup.
            logger.debug(
                'join_broadcaster before SFU egress source=%s: %s',
                self.source_id,
                exc,
            )

        video_ssrc = _VIDEO_SSRC_BASE + random.randint(1, 50_000)
        self._video_transport = self.client.create_plain_transport(
            self.room_id,
            self.compositor_peer_id,
            direction='producer',
            comedia=True,
            rtcp_mux=False,
        )
        video_producer = self.client.create_producer(
            self.room_id,
            self.compositor_peer_id,
            transport_id=self._video_transport['transportId'],
            kind='video',
            rtp_parameters=_default_video_rtp_parameters(ssrc=video_ssrc),
            app_data={'source': 'video', 'sourceId': self.source_id},
        )
        self._video_producer_id = video_producer.get('producerId')

        logger.info(
            'SFU URI video producer created source=%s producer=%s rtp=%s:%s rtcp=%s '
            '(TODO: GStreamer tee/x264enc/rtph264pay/udpsink → this target)',
            self.source_id,
            self._video_producer_id,
            self._video_transport.get('ip'),
            self._video_transport.get('port'),
            self._video_transport.get('rtcpPort'),
        )

        if self.include_audio:
            audio_ssrc = _AUDIO_SSRC_BASE + random.randint(1, 50_000)
            self._audio_transport = self.client.create_plain_transport(
                self.room_id,
                self.compositor_peer_id,
                direction='producer',
                comedia=True,
                rtcp_mux=False,
            )
            audio_producer = self.client.create_producer(
                self.room_id,
                self.compositor_peer_id,
                transport_id=self._audio_transport['transportId'],
                kind='audio',
                rtp_parameters=_default_audio_rtp_parameters(ssrc=audio_ssrc),
                app_data={'source': 'audio', 'sourceId': self.source_id},
            )
            self._audio_producer_id = audio_producer.get('producerId')
            logger.info(
                'SFU URI audio producer created source=%s producer=%s rtp=%s:%s rtcp=%s '
                '(TODO: GStreamer tee/opusenc/rtpopuspay/udpsink → this target)',
                self.source_id,
                self._audio_producer_id,
                self._audio_transport.get('ip'),
                self._audio_transport.get('port'),
                self._audio_transport.get('rtcpPort'),
            )

        self._started = True

    def stop(self) -> None:
        """Best-effort local teardown. Mediasoup producers close with peer/transport."""
        if not self._started:
            return
        logger.info(
            'Stopping SFU URI egress for source=%s (video=%s audio=%s)',
            self.source_id,
            self._video_producer_id,
            self._audio_producer_id,
        )
        self._video_transport = None
        self._audio_transport = None
        self._video_producer_id = None
        self._audio_producer_id = None
        self._started = False
