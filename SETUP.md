# System dependencies and bootstrap guide for compositor-backend.

## Python

- Python 3.11+
- Virtualenv: `python -m venv .venv && source .venv/bin/activate`

## GStreamer (required for Phase 3+)

### macOS

```bash
brew install gstreamer gst-plugins-base gst-plugins-good gst-plugins-bad \
  gst-libav pygobject3 gtk+3
```

### Linux (Debian/Ubuntu)

```bash
sudo apt install \
  python3-gi gir1.2-gstreamer-1.0 \
  gstreamer1.0-tools gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
  gstreamer1.0-libav
```

## Video mix backends (CPU / OpenGL / CUDA)

Set `COMPOSITOR_VIDEO_BACKEND` in `.env`:

| Value | Mixer | Notes |
|-------|--------|--------|
| `cpu` (safe default for laptops) | `compositor` | Always available with base plugins |
| `gl` | `glcompositor` | Portable GPU path (macOS / Linux / Windows). Needs GL plugins (`glupload`, `glcolorconvert`, `gldownload`). |
| `cuda` | `cudacompositor` | NVIDIA only (e.g. production A16). Needs GStreamer **≥ 1.26** nvcodec plugins (`cudaupload`, `cudadownload`) + NVIDIA driver/CUDA. |
| `auto` (default) | first available of cuda → gl → cpu | Falls back quietly when GPU plugins are missing |

Optional: `COMPOSITOR_CUDA_DEVICE_ID=-1` (auto) or a device index for `cudacompositor`.

Verify plugins:

```bash
gst-inspect-1.0 compositor
gst-inspect-1.0 glcompositor
gst-inspect-1.0 cudacompositor
```

Health (`GET /api/v1/health/`) reports `requested_backend`, `resolved_backend`, and element availability.

## Python packages

```bash
pip install -r requirements.txt
```

> **Note:** `PyGObject` requires system GObject/GStreamer libraries. If `pip install PyGObject` fails on macOS, use `brew install pygobject3` and ensure `GI_TYPELIB_PATH` includes Homebrew typelibs.

## Environment

```bash
cp .env.example .env
```

## Run

```bash
python manage.py migrate
python manage.py runserver 8000
```

Health check: `GET http://localhost:8000/api/v1/health/`

Ingest status (Phase 3): `GET http://localhost:8000/api/v1/sessions/{sessionId}/ingest/`

## RTP ingest ports

Compositor listens on UDP ports `50000–50999` by default (configurable via `COMPOSITOR_RTP_PORT_MIN/MAX`).
Mediasoup sends PlainTransport RTP to `COMPOSITOR_RTP_HOST`.
