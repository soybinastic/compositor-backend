"""HTTP client adapter for the mediasoup Broadcaster API."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from django.conf import settings

from integrations.mediasoup.exceptions import MediasoupApiError

COMPOSITOR_DEVICE: dict[str, str] = {
    'name': 'Compositor',
    'flag': 'gstreamer',
}


class MediasoupHttpClient:
    """
    Synchronous HTTP client for the mediasoup-demo server API.

    Used by compositor-backend to bootstrap rooms and register the
    compositor as a BroadcasterPeer.
    """

    def __init__(
        self,
        api_url: str | None = None,
        origin: str | None = None,
    ) -> None:
        self.api_url = (api_url or settings.MEDIASOUP_API_URL).rstrip('/')
        self.origin = origin or settings.MEDIASOUP_ORIGIN

    def create_room(self, room_id: str) -> dict[str, Any]:
        """Create a mediasoup room for a studio session."""
        result = self._request('POST', '/rooms', {'roomId': room_id})
        return result if isinstance(result, dict) else {'roomId': room_id}

    def delete_room(self, room_id: str) -> None:
        """Tear down a mediasoup room."""
        self._request('DELETE', f'/rooms/{room_id}')

    def get_router_rtp_capabilities(self, room_id: str) -> dict[str, Any]:
        """Fetch router RTP capabilities for a room."""
        result = self._request('GET', f'/rooms/{room_id}')
        return result if isinstance(result, dict) else {}

    def get_producers(self, room_id: str) -> dict[str, Any]:
        """List all active producers in a room."""
        result = self._request('GET', f'/rooms/{room_id}/producers')
        return result if isinstance(result, dict) else {'peerProducersInfos': []}

    def create_broadcaster(
        self,
        room_id: str,
        peer_id: str,
        *,
        display_name: str = 'Compositor',
        device: dict[str, str] | None = None,
    ) -> None:
        """Register the compositor as a BroadcasterPeer in the room."""
        self._request(
            'POST',
            f'/rooms/{room_id}/broadcasters',
            {
                'peerId': peer_id,
                'displayName': display_name,
                'device': device or COMPOSITOR_DEVICE,
            },
        )

    def delete_broadcaster(self, room_id: str, peer_id: str) -> None:
        """Disconnect a BroadcasterPeer from the room."""
        self._request('DELETE', f'/rooms/{room_id}/broadcasters/{peer_id}')

    def join_broadcaster(self, room_id: str, peer_id: str) -> None:
        """Join a BroadcasterPeer after PlainTransports are configured."""
        self._request('POST', f'/rooms/{room_id}/broadcasters/{peer_id}/join')

    def create_plain_transport(
        self,
        room_id: str,
        peer_id: str,
        *,
        direction: str = 'consumer',
        comedia: bool = False,
        rtcp_mux: bool = False,
    ) -> dict[str, Any]:
        """Create a PlainTransport for RTP ingest or egress."""
        result = self._request(
            'POST',
            f'/rooms/{room_id}/broadcasters/{peer_id}/transports',
            {
                'direction': direction,
                'comedia': comedia,
                'rtcpMux': rtcp_mux,
            },
        )
        return result if isinstance(result, dict) else {}

    def connect_plain_transport(
        self,
        room_id: str,
        peer_id: str,
        transport_id: str,
        *,
        ip: str,
        port: int,
        rtcp_port: int | None = None,
        rtcp_mux: bool = False,
    ) -> None:
        """Tell mediasoup where to send RTP packets."""
        body: dict[str, Any] = {'ip': ip, 'port': port}
        if not rtcp_mux:
            if rtcp_port is None:
                raise ValueError('rtcp_port is required when rtcp_mux is disabled')
            body['rtcpPort'] = rtcp_port

        self._request(
            'POST',
            f'/rooms/{room_id}/broadcasters/{peer_id}/transports/{transport_id}/connect',
            body,
        )

    def create_consumer(
        self,
        room_id: str,
        peer_id: str,
        *,
        transport_id: str,
        producer_id: str,
        rtp_capabilities: dict[str, Any],
        paused: bool = False,
    ) -> dict[str, Any]:
        """Create a Consumer to receive a remote Producer via PlainTransport."""
        result = self._request(
            'POST',
            f'/rooms/{room_id}/broadcasters/{peer_id}/consumers',
            {
                'transportId': transport_id,
                'producerId': producer_id,
                'paused': paused,
                'rtpCapabilities': rtp_capabilities,
            },
        )
        return result if isinstance(result, dict) else {}

    def resume_consumer(
        self,
        room_id: str,
        peer_id: str,
        consumer_id: str,
    ) -> None:
        """Resume a paused Consumer (required for video after GStreamer is ready)."""
        self._request(
            'POST',
            f'/rooms/{room_id}/broadcasters/{peer_id}/consumers/{consumer_id}/resume',
        )

    def _request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
    ) -> Any:
        url = f'{self.api_url}{path}'
        headers = {
            'Origin': self.origin,
            'Content-Type': 'application/json',
        }
        body = json.dumps(data).encode('utf-8') if data is not None else None
        request = urllib.request.Request(
            url,
            data=body,
            headers=headers,
            method=method,
        )

        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                if response.status == 204:
                    return None

                raw = response.read()
                if not raw:
                    return None

                content_type = response.headers.get('Content-Type', '')
                if 'application/json' in content_type:
                    return json.loads(raw.decode('utf-8'))

                return raw.decode('utf-8')
        except urllib.error.HTTPError as exc:
            message = exc.read().decode('utf-8', errors='replace')
            raise MediasoupApiError(exc.code, message) from exc
        except urllib.error.URLError as exc:
            raise MediasoupApiError(0, str(exc.reason)) from exc

    def __repr__(self) -> str:
        return f'MediasoupHttpClient(api_url={self.api_url!r})'
