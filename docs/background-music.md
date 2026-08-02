# Background Music API Contract

Scene-scoped configuration and session-scoped runtime playback for studio background music.

## Scene configuration (persisted)

PATCH `/api/v1/sessions/{session_id}/scenes/{scene_id}/`

```json
{
  "background_music": {
    "version": 1,
    "enabled": false,
    "track": {
      "asset_id": "8e4211f5-0c86-4d87-9b9b-0191b1db7b85",
      "url": "https://studio-assets.b-cdn.net/bgm/twilight_drift.mp3",
      "title": "Twilight Drift"
    },
    "volume": 0.5,
    "loop": true,
    "muted": false
  }
}
```

Selecting or replacing a track saves config and **starts preview playback in the browser** immediately. The compositor program mix is updated in the background (best-effort; preview and recording may drift).

Frontend uses a hidden `HTMLAudioElement` for studio preview. Compositor GStreamer mix is used for recording and live stream.

## Runtime state (compositor-owned)

GET `/api/v1/sessions/{session_id}/background-music/` *(Epic 4)*

```json
{
  "scene_id": "uuid",
  "playback_state": "ready",
  "position_ms": 0,
  "duration_ms": 180000,
  "error": null,
  "updated_at": "2026-07-31T12:00:00+00:00"
}
```

## Transport commands (compositor-owned)

POST `/api/v1/sessions/{session_id}/scenes/{scene_id}/background-music/{play|pause|resume|stop|volume}/`

Only the active scene may control live playback. Returns `503` when the compositor worker is not running.

```json
{
  "accepted": true,
  "state": {
    "scene_id": "uuid",
    "playback_state": "playing",
    "position_ms": 0,
    "duration_ms": 180000,
    "error": null,
    "updated_at": "2026-07-31T12:00:00+00:00"
  },
  "rejection_reason": null
}
```

When rejected (e.g. inactive scene or no track loaded), `accepted` is `false` and `rejection_reason` is set.

## Pipeline implementation

- GStreamer branch: `uridecodebin -> audioconvert -> audioresample -> volume -> queue -> audiomixer`
- Volume changes update the `volume` element only
- Scene activation applies config via compositor command *(Epic 5 — implemented)*

## Supported formats

`.mp3`, `.wav`, `.aac`, `.m4a`, `.ogg`
