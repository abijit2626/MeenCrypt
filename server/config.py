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
    FISHRAND_USB_CODE       path to the universal USB code (code.txt). When
                            set, the server auto-reads it for encrypt/decrypt.
    FISHRAND_DIARY_PATH     where the diary app's persistent encrypted
                            package lives on disk (default data/diary.pkg).
                            Ciphertext only - the plaintext is never written
                            here; see server/main.py's /api/diary/* routes.
"""

from __future__ import annotations

import os
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

FISH_URL = os.getenv("FISHRAND_FISH_URL", "").strip() or None
WATCH_INBOX = os.getenv("FISHRAND_FISH_INBOX") == "1"
INBOX_DIR = pathlib.Path(os.getenv("FISHRAND_FISH_INBOX_DIR", REPO_ROOT / "data" / "inbox"))
VISION_FRAMES = max(1, int(os.getenv("FISHRAND_VISION_FRAMES", "60")))
USB_CODE = os.getenv("FISHRAND_USB_CODE", "").strip() or None
_SAMPLE_FALLBACK = REPO_ROOT / "examples" / "sample_vision_data.json"
DEFAULT_FALLBACK = pathlib.Path(os.getenv("FISHRAND_FISH_FALLBACK", _SAMPLE_FALLBACK))
DIARY_PATH = pathlib.Path(os.getenv("FISHRAND_DIARY_PATH", REPO_ROOT / "data" / "diary.pkg"))