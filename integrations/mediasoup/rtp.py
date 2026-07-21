"""RTP capability helpers for mediasoup consumer creation."""

from __future__ import annotations

from typing import Any


def get_codec_payload_type(router_caps: dict[str, Any], mime_type: str) -> int:
    """Return the payload type for a codec in router RTP capabilities."""
    for codec in router_caps.get('codecs', []):
        if codec.get('mimeType', '').lower() == mime_type.lower():
            payload = codec.get('preferredPayloadType', codec.get('payloadType'))
            if payload is not None:
                return int(payload)

    raise ValueError(f'Codec not found in router capabilities: {mime_type}')


def get_payload_type_from_rtp_parameters(rtp_parameters: dict[str, Any]) -> int:
    """Return the wire payload type from a mediasoup Consumer rtpParameters."""
    codecs = rtp_parameters.get('codecs') or []
    if not codecs:
        raise ValueError('rtpParameters has no codecs')

    payload = codecs[0].get('payloadType')
    if payload is None:
        raise ValueError('rtpParameters codec is missing payloadType')

    return int(payload)


def build_audio_rtp_capabilities(payload_type: int) -> dict[str, Any]:
    return {
        'codecs': [
            {
                'kind': 'audio',
                'mimeType': 'audio/opus',
                'preferredPayloadType': payload_type,
                'clockRate': 48000,
                'channels': 2,
                'parameters': {'useinbandfec': 1},
            }
        ]
    }


def build_video_rtp_capabilities(payload_type: int) -> dict[str, Any]:
    return {
        'codecs': [
            {
                'kind': 'video',
                'mimeType': 'video/VP8',
                'preferredPayloadType': payload_type,
                'clockRate': 90000,
                'parameters': {},
                'rtcpFeedback': [{'type': 'nack'}],
            }
        ]
    }
