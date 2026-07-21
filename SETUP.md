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

Optional: `COMPOSITOR_DISABLE_BACKGROUND=true` disables background image/video graphics even when configured.

## Graphics and overlays

Session-scoped APIs under `/api/v1/sessions/{sessionId}/graphics/`:

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/graphics/` | Current graphics state |
| POST | `/graphics/bulk/` | Upsert any subset of layers |
| POST | `/graphics/background/` | Background image/video |
| POST | `/graphics/overlay/` | Full-frame / positioned overlay |
| POST | `/graphics/logo/` | Corner logo |
| POST | `/graphics/qr/` | QR code |
| POST | `/graphics/banner/` | Lower-third banner |
| POST | `/graphics/ticker/` | Scrolling ticker |
| POST | `/graphics/banner-ticker/` | Banner + ticker together |
| POST | `/graphics/chat/` | Chat overlay panel |

Graphics are mixed onto the same GStreamer canvas as live video (and therefore appear in recording/streaming). Layers use dedicated mixer pads with fixed z-order; updates hot-swap content without rebuilding participant ingest.

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
pip install Pillow   # required for banner / ticker / chat rendering
```

> **Note:** Add `Pillow` to `requirements.txt` if it is not already listed (banner, ticker, and chat overlays render via PIL).

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
