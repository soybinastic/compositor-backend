"""SFU PlainTransport produce path for compositor-owned URI video sources.

Flow (mediasoup BroadcasterPeer producer direction):
1. create_plain_transport(direction='producer', comedia=True) → listen ip/port
2. create_producer(kind, rtpParameters, appData={source, sourceId})
3. GStreamer encodes and sends RTP via udpsink to the transport tuple

Host/guests then consume those producers over WebRTC like any remote track.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import Any

import gi

gi.require_version('Gst', '1.0')
from gi.repository import Gst  # noqa: E402

from integrations.mediasoup.client import MediasoupHttpClient

logger = logging.getLogger(__name__)

_VIDEO_SSRC_BASE = 200_000
_AUDIO_SSRC_BASE = 100_000
_VIDEO_PT = 101
_AUDIO_PT = 100
_VIDEO_BITRATE_KBPS = 1500


def _default_video_rtp_parameters(*, payload_type: int = _VIDEO_PT, ssrc: int) -> dict[str, Any]:
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


def _default_audio_rtp_parameters(*, payload_type: int = _AUDIO_PT, ssrc: int) -> dict[str, Any]:
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


def _make_encoder(factory_names: tuple[str, ...], name: str) -> Gst.Element:
    for factory_name in factory_names:
        element = Gst.ElementFactory.make(factory_name, name)
        if element is not None:
            return element
    raise RuntimeError(f'Failed to create encoder for {name} (tried {factory_names})')


def _configure_leak_queue(queue: Gst.Element) -> None:
    queue.set_property('leaky', 2)
    queue.set_property('max-size-time', 500 * Gst.MSECOND)
    queue.set_property('max-size-buffers', 0)
    queue.set_property('max-size-bytes', 0)


def _configure_x264(venc: Gst.Element) -> None:
    factory_name = venc.get_factory().get_name() if venc.get_factory() else ''
    if factory_name == 'x264enc':
        venc.set_property('speed-preset', 'veryfast')
        venc.set_property('tune', 'zerolatency')
        venc.set_property('key-int-max', 60)
        venc.set_property('bitrate', _VIDEO_BITRATE_KBPS)
        if venc.find_property('byte-stream') is not None:
            venc.set_property('byte-stream', True)
    elif factory_name == 'openh264enc':
        venc.set_property('bitrate', _VIDEO_BITRATE_KBPS * 1000)


def _configure_udpsink(sink: Gst.Element, *, host: str, port: int) -> None:
    sink.set_property('host', host)
    sink.set_property('port', int(port))
    sink.set_property('sync', False)
    sink.set_property('async', False)


@dataclass
class UriSfuRtpSendBranch:
    """GStreamer subgraph that encodes a URI source tee into mediasoup PlainTransports."""

    elements: list[Gst.Element] = field(default_factory=list)
    video_tee_pad: Gst.Pad | None = None
    audio_tee_pad: Gst.Pad | None = None


@dataclass
class SfuSourceEgress:
    """Registers compositor URI media as SFU producers and sends RTP."""

    client: MediasoupHttpClient
    room_id: str
    compositor_peer_id: str
    source_id: str
    include_audio: bool = True
    _video_transport: dict[str, Any] | None = field(default=None, init=False, repr=False)
    _audio_transport: dict[str, Any] | None = field(default=None, init=False, repr=False)
    _video_producer_id: str | None = field(default=None, init=False, repr=False)
    _audio_producer_id: str | None = field(default=None, init=False, repr=False)
    _video_ssrc: int | None = field(default=None, init=False, repr=False)
    _audio_ssrc: int | None = field(default=None, init=False, repr=False)
    _rtp_branch: UriSfuRtpSendBranch | None = field(default=None, init=False, repr=False)
    _pipeline: Gst.Pipeline | None = field(default=None, init=False, repr=False)
    _video_tee: Gst.Element | None = field(default=None, init=False, repr=False)
    _audio_tee: Gst.Element | None = field(default=None, init=False, repr=False)
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

        try:
            self.client.ensure_broadcaster_joined(self.room_id, self.compositor_peer_id)
        except Exception as exc:
            logger.debug(
                'ensure_broadcaster_joined before SFU egress source=%s: %s',
                self.source_id,
                exc,
            )

        self._video_ssrc = _VIDEO_SSRC_BASE + random.randint(1, 50_000)
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
            rtp_parameters=_default_video_rtp_parameters(ssrc=self._video_ssrc),
            app_data={'source': 'video', 'sourceId': self.source_id},
        )
        self._video_producer_id = video_producer.get('producerId')

        logger.info(
            'SFU URI video producer created source=%s producer=%s rtp=%s:%s rtcp=%s',
            self.source_id,
            self._video_producer_id,
            self._video_transport.get('ip'),
            self._video_transport.get('port'),
            self._video_transport.get('rtcpPort'),
        )

        if self.include_audio:
            self._audio_ssrc = _AUDIO_SSRC_BASE + random.randint(1, 50_000)
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
                rtp_parameters=_default_audio_rtp_parameters(ssrc=self._audio_ssrc),
                app_data={'source': 'audio', 'sourceId': self.source_id},
            )
            self._audio_producer_id = audio_producer.get('producerId')
            logger.info(
                'SFU URI audio producer created source=%s producer=%s rtp=%s:%s rtcp=%s',
                self.source_id,
                self._audio_producer_id,
                self._audio_transport.get('ip'),
                self._audio_transport.get('port'),
                self._audio_transport.get('rtcpPort'),
            )

        self._started = True

    def attach_rtp_send(
        self,
        pipeline: Gst.Pipeline,
        *,
        video_tee: Gst.Element,
        audio_tee: Gst.Element | None = None,
    ) -> None:
        """Link encode → rtppay → udpsink chains onto URI source tees."""
        if not self._started:
            raise RuntimeError('SfuSourceEgress.start() must run before attach_rtp_send')
        if self._rtp_branch is not None:
            return
        if self._video_transport is None or self._video_ssrc is None:
            raise RuntimeError('Video PlainTransport missing for SFU egress')

        safe_id = self.source_id.replace('-', '_')
        branch = UriSfuRtpSendBranch()

        v_queue = Gst.ElementFactory.make('queue', f'sfu_v_queue_{safe_id}')
        v_convert = Gst.ElementFactory.make('videoconvert', f'sfu_v_convert_{safe_id}')
        venc = _make_encoder(('x264enc', 'openh264enc'), f'sfu_venc_{safe_id}')
        h264parse = Gst.ElementFactory.make('h264parse', f'sfu_h264parse_{safe_id}')
        pay = Gst.ElementFactory.make('rtph264pay', f'sfu_v_pay_{safe_id}')
        v_sink = Gst.ElementFactory.make('udpsink', f'sfu_v_udpsink_{safe_id}')

        if not all([v_queue, v_convert, venc, h264parse, pay, v_sink]):
            raise RuntimeError(f'Failed to create SFU video RTP elements for {self.source_id}')

        _configure_leak_queue(v_queue)
        _configure_x264(venc)
        if h264parse.find_property('config-interval') is not None:
            h264parse.set_property('config-interval', 1)
        pay.set_property('pt', _VIDEO_PT)
        pay.set_property('ssrc', int(self._video_ssrc))
        if pay.find_property('config-interval') is not None:
            pay.set_property('config-interval', 1)
        _configure_udpsink(
            v_sink,
            host=str(self._video_transport['ip']),
            port=int(self._video_transport['port']),
        )

        video_elements = [v_queue, v_convert, venc, h264parse, pay, v_sink]
        branch.elements.extend(video_elements)

        audio_elements: list[Gst.Element] = []
        if (
            self.include_audio
            and audio_tee is not None
            and self._audio_transport is not None
            and self._audio_ssrc is not None
        ):
            a_queue = Gst.ElementFactory.make('queue', f'sfu_a_queue_{safe_id}')
            a_convert = Gst.ElementFactory.make('audioconvert', f'sfu_a_convert_{safe_id}')
            a_resample = Gst.ElementFactory.make('audioresample', f'sfu_a_resample_{safe_id}')
            aenc = Gst.ElementFactory.make('opusenc', f'sfu_aenc_{safe_id}')
            a_pay = Gst.ElementFactory.make('rtpopuspay', f'sfu_a_pay_{safe_id}')
            a_sink = Gst.ElementFactory.make('udpsink', f'sfu_a_udpsink_{safe_id}')
            if not all([a_queue, a_convert, a_resample, aenc, a_pay, a_sink]):
                raise RuntimeError(f'Failed to create SFU audio RTP elements for {self.source_id}')
            _configure_leak_queue(a_queue)
            a_pay.set_property('pt', _AUDIO_PT)
            a_pay.set_property('ssrc', int(self._audio_ssrc))
            _configure_udpsink(
                a_sink,
                host=str(self._audio_transport['ip']),
                port=int(self._audio_transport['port']),
            )
            audio_elements = [a_queue, a_convert, a_resample, aenc, a_pay, a_sink]
            branch.elements.extend(audio_elements)

        for element in branch.elements:
            pipeline.add(element)

        def _link_chain(elements: list[Gst.Element], *, label: str) -> None:
            for upstream, downstream in zip(elements, elements[1:]):
                if not upstream.link(downstream):
                    raise RuntimeError(f'Failed to link {label} ({upstream.name} → {downstream.name})')

        _link_chain(video_elements, label=f'sfu-video-{self.source_id}')
        if audio_elements:
            _link_chain(audio_elements, label=f'sfu-audio-{self.source_id}')

        video_tee_pad = video_tee.get_request_pad('src_%u')
        if video_tee_pad is None:
            raise RuntimeError(f'Failed to request video tee pad for SFU egress {self.source_id}')
        v_sink_pad = v_queue.get_static_pad('sink')
        if v_sink_pad is None or video_tee_pad.link(v_sink_pad) != Gst.PadLinkReturn.OK:
            raise RuntimeError(f'Failed to link video tee → SFU queue for {self.source_id}')
        branch.video_tee_pad = video_tee_pad

        if audio_elements and audio_tee is not None:
            audio_tee_pad = audio_tee.get_request_pad('src_%u')
            if audio_tee_pad is None:
                raise RuntimeError(f'Failed to request audio tee pad for SFU egress {self.source_id}')
            a_sink_pad = audio_elements[0].get_static_pad('sink')
            if a_sink_pad is None or audio_tee_pad.link(a_sink_pad) != Gst.PadLinkReturn.OK:
                raise RuntimeError(f'Failed to link audio tee → SFU queue for {self.source_id}')
            branch.audio_tee_pad = audio_tee_pad

        for element in branch.elements:
            element.sync_state_with_parent()

        self._rtp_branch = branch
        self._pipeline = pipeline
        self._video_tee = video_tee
        self._audio_tee = audio_tee
        logger.info(
            'SFU URI RTP send attached source=%s video=%s:%s audio=%s',
            self.source_id,
            self._video_transport.get('ip'),
            self._video_transport.get('port'),
            bool(audio_elements),
        )

    def stop(self) -> None:
        """Detach RTP send graph and clear local producer bookkeeping."""
        if not self._started and self._rtp_branch is None:
            return

        branch = self._rtp_branch
        if branch is not None:
            if branch.video_tee_pad is not None and self._video_tee is not None:
                peer = branch.video_tee_pad.get_peer()
                if peer is not None:
                    branch.video_tee_pad.unlink(peer)
                self._video_tee.release_request_pad(branch.video_tee_pad)
                branch.video_tee_pad = None
            if branch.audio_tee_pad is not None and self._audio_tee is not None:
                peer = branch.audio_tee_pad.get_peer()
                if peer is not None:
                    branch.audio_tee_pad.unlink(peer)
                self._audio_tee.release_request_pad(branch.audio_tee_pad)
                branch.audio_tee_pad = None
            if self._pipeline is not None:
                for element in branch.elements:
                    element.set_state(Gst.State.NULL)
                    self._pipeline.remove(element)

        logger.info(
            'Stopping SFU URI egress for source=%s (video=%s audio=%s)',
            self.source_id,
            self._video_producer_id,
            self._audio_producer_id,
        )
        self._rtp_branch = None
        self._pipeline = None
        self._video_tee = None
        self._audio_tee = None
        self._video_transport = None
        self._audio_transport = None
        self._video_producer_id = None
        self._audio_producer_id = None
        self._video_ssrc = None
        self._audio_ssrc = None
        self._started = False
