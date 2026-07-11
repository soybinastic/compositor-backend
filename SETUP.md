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
