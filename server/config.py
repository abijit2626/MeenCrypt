"""FISHRAND server configuration.

Collector behaviour is env-configurable so the deploy can adapt the day the
fish-vision repo merges:

    FISHRAND_FISH_URL       http endpoint that returns fish observations
                            JSON (the vision engine's service).
    FISHRAND_FISH_INBOX     1 → watch data/inbox/*.json for NDJSON dropped
                            by the vision engine (its fish_log.json).
    FISHRAND_FISH_INBOX_DIR path to that inbox (default data/inbox).
    FISHRAND_VISION_FRAMES  number of recent vision frames to bundle into
                            one encryption window (default 60).
    FISHRAND_FISH_FALLBACK  path to a fallback observations JSON (defaults
                            to examples/sample_vision_data.json).
    FISHRAND_PUBLIC_KEY_PATH where the server keeps the RSA PUBLIC key it
                            encrypts with (default data/keys/public_key.pem).
                            The server NEVER stores a private key: unlocking
                            requires the caller to supply their own.
    FISHRAND_CORS_ORIGINS   comma-separated allowed browser origins
                            (default the local dashboard + diary dev ports).
    FISHRAND_DIARY_PATH     where the diary app's persistent encrypted
                            package lives on disk (default data/diary.pkg).
                            Ciphertext only - the plaintext is never written
                            here; see server/main.py's /api/diary/* routes.

    FISHRAND_AUDIO_PORT     serial device for the ESP32 mic (e.g.
                            /dev/ttyUSB0, /dev/ttyACM0). Unset -> audio
                            capture is skipped entirely (fish-only unless
                            fish quality requires audio, in which case
                            encryption fails clearly - see fishrand/api.py
                            encrypt_with_observation).
    FISHRAND_AUDIO_BAUD     ESP32 serial baud rate (default 115200 - do not
                            change unless the firmware itself changes).
    FISHRAND_AUDIO_WINDOW_S how many wall-clock seconds of Sound_Level
                            readings to collect per encryption (default 2.0,
                            matching vision.py's JSON_LOG_INTERVAL - one
                            fish frame interval; live audio can't be
                            bundled like historical fish frames the way
                            FISHRAND_VISION_FRAMES bundles a multi-frame
                            window).
    FISHRAND_AUDIO_MIN_READINGS minimum valid Sound_Level lines required in
                            that window before the capture is trusted
                            (default 3 - guards against a wrong baud rate
                            or a near-disconnected ESP32).

    FISHRAND_VISION_POLL_S  how often (seconds) the background poller
                            re-checks the configured fish sources and
                            republishes to the dashboard's live feed panel
                            + SSE stream when the window actually changed
                            (default 1.0 - deliberately faster than
                            vision.py's own 2s JSON_LOG_INTERVAL so a fresh
                            frame is never more than ~1s stale by the time
                            this poller notices it). This is what makes the
                            dashboard's "Live fish vision feed" panel
                            update on its own instead of staying on
                            "Waiting for a vision window" forever - see
                            server/main.py's background poll thread.
                            Unrelated to encryption itself, which always
                            uses whatever _resolve_fish() resolves at that
                            moment (the same live/cached window this
                            poller keeps warm).

    Fish-quality thresholds (fishrand/quality.py classify_fish_quality) -
    NOT derived from any existing calibrated threshold (vision.py only has
    a visualization-only speed>0.5 arrow-drawing cutoff); these are
    reasonable starting points to tune against a real camera/tank:
    FISHRAND_QUALITY_GOOD_FISH_MIN       (default 2)
    FISHRAND_QUALITY_GOOD_ACTIVITY_MIN   (default 5.0, on the 0-100 scale)
    FISHRAND_QUALITY_MEDIUM_FISH_MIN     (default 1)
    FISHRAND_QUALITY_MEDIUM_ACTIVITY_MIN (default 1.0, on the 0-100 scale)
"""

from __future__ import annotations

import os
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

FISH_URL = os.getenv("FISHRAND_FISH_URL", "").strip() or None
WATCH_INBOX = os.getenv("FISHRAND_FISH_INBOX") == "1"
INBOX_DIR = pathlib.Path(os.getenv("FISHRAND_FISH_INBOX_DIR", REPO_ROOT / "data" / "inbox"))
VISION_FRAMES = max(1, int(os.getenv("FISHRAND_VISION_FRAMES", "60")))
_SAMPLE_FALLBACK = REPO_ROOT / "examples" / "sample_vision_data.json"
DEFAULT_FALLBACK = pathlib.Path(os.getenv("FISHRAND_FISH_FALLBACK", _SAMPLE_FALLBACK))
DIARY_PATH = pathlib.Path(os.getenv("FISHRAND_DIARY_PATH", REPO_ROOT / "data" / "diary.pkg"))

# The server holds ONLY the public key (it can encrypt, never decrypt).
PUBLIC_KEY_PATH = pathlib.Path(
    os.getenv("FISHRAND_PUBLIC_KEY_PATH", REPO_ROOT / "data" / "keys" / "public_key.pem")
)

# Local-only deployment: allow just the dashboard/diary dev origins rather
# than "*" (no auth layer here, so don't let arbitrary pages call the API).
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "FISHRAND_CORS_ORIGINS", "http://localhost:5173,http://localhost:5174"
    ).split(",")
    if origin.strip()
]

# ESP32 + INMP441 audio (server/audio_serial.py).
AUDIO_SERIAL_PORT = os.getenv("FISHRAND_AUDIO_PORT", "").strip() or None
AUDIO_BAUD_RATE = int(os.getenv("FISHRAND_AUDIO_BAUD", "115200"))
AUDIO_WINDOW_DURATION_S = float(os.getenv("FISHRAND_AUDIO_WINDOW_S", "2.0"))
AUDIO_MIN_READINGS = max(1, int(os.getenv("FISHRAND_AUDIO_MIN_READINGS", "3")))

# Background dashboard-feed poller (server/main.py) - purely cosmetic/live
# display, never gates encryption itself.
VISION_POLL_INTERVAL_S = max(0.5, float(os.getenv("FISHRAND_VISION_POLL_S", "1.0")))

# Fish-quality classification (fishrand/quality.py). See module docstring
# above for why these defaults are starting points, not calibrated values.
FISH_QUALITY_GOOD_FISH_COUNT_MIN = max(0, int(os.getenv("FISHRAND_QUALITY_GOOD_FISH_MIN", "2")))
FISH_QUALITY_GOOD_ACTIVITY_PCT_MIN = float(os.getenv("FISHRAND_QUALITY_GOOD_ACTIVITY_MIN", "5.0"))
FISH_QUALITY_MEDIUM_FISH_COUNT_MIN = max(0, int(os.getenv("FISHRAND_QUALITY_MEDIUM_FISH_MIN", "1")))
FISH_QUALITY_MEDIUM_ACTIVITY_PCT_MIN = float(os.getenv("FISHRAND_QUALITY_MEDIUM_ACTIVITY_MIN", "1.0"))