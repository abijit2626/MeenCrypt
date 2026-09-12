"""FISHRAND server - FastAPI backend streaming pipeline events via SSE.

The dashboard triggers encryption; this server collects the fish
observations itself (see server.collector) at encrypt time.

The security model: the server keeps only the RSA PUBLIC key, so it can
encrypt but can never decrypt. Unlocking always requires the caller to
supply their own private key PEM, which is used in memory for that one
request and never written to disk.

Endpoints:
    GET  /api/health           liveness + version
    GET  /api/events           persistent SSE stream: fish_update events
    POST /api/observations     push channel for the vision engine's output
    GET  /api/observations/current
    GET  /api/audio/events     persistent SSE stream: audio_update events
    GET  /api/audio/current    latest ESP32 mic window (dashboard display)
    POST /api/keys/generate    body {force?}; makes an RSA-3072 keypair,
                               stores ONLY the public key, and returns the
                               private key PEM as a download (never saved).
    POST /api/encrypt          body {plaintext}; server collects the fish
                               (plus ESP32 audio when the fish are too
                               still) and encrypts for its public key (SSE).
    POST /api/decrypt          body {package, private_key_pem}; unwraps with
                               the caller's key, verifies GCM tag (SSE).
    GET  /api/diary            current state of the diary app's ONE
                               persistent encrypted document on this PC
                               (ciphertext + metadata only - never plaintext).
    POST /api/diary/save       body {plaintext}; encrypts (same path as
                               /api/encrypt) and OVERWRITES the stored
                               document on disk (SSE).
    POST /api/diary/unlock     body {private_key_pem}; decrypts the stored
                               document in place (SSE).

LIVE DASHBOARD FEEDS:
    Two daemon background threads (started in the FastAPI lifespan below)
    keep the dashboard's "live" panels populated without any user action:
      - fish poller: re-checks the configured fish source(s) every
        config.VISION_POLL_INTERVAL_S seconds and republishes to the same
        broker/SSE stream a manual POST /api/observations would use.
      - audio poller: only runs if config.AUDIO_SERIAL_PORT is configured;
        holds the ESP32 serial port open and continuously republishes each
        Sound_Level window to its own broker/SSE stream.
    Both are display-only conveniences - _resolve_fish()/_resolve_audio()
    (used by actual encryption) simply read whatever these pollers last
    published, same as before a manual push, so encryption behaviour is
    unchanged by their presence.

Run:  uvicorn server.main:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import os
import statistics
import sys
import threading
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

import fishrand
from fishrand import (
    AuthenticationFailure,
    decrypt_rsa_hybrid_package,
    encrypt_with_observation,
)
from fishrand.observe import count_observations, observation_stats
from fishrand.package import save_package
from fishrand.rsa_hybrid import (
    generate_keypair,
    load_private_key_from_pem,
    load_public_key,
    serialize_private_key,
    serialize_public_key,
)
from fishrand.schema import SchemaError, validate_observations

from . import config
from .audio_serial import ESP32SerialReader, SerialReaderConfig, SerialReaderError, to_audio_observation
from .collector import FishSourceError, collect_fish_verbose, current_source, _normalize_raw

# --------------------------------------------------------------------------
# Background dashboard-feed pollers (fish + audio) - see module docstring.
# --------------------------------------------------------------------------
_LOOP: asyncio.AbstractEventLoop | None = None
_STOP_EVENT = threading.Event()
_AUDIO_PORT_LOCK = threading.Lock()  # only one thing may hold the ESP32 serial port at a time


def _schedule(fn, *args) -> None:
    """Run fn(*args) on the FastAPI event loop thread, safe to call from a
    background poller thread (asyncio.Queue is not thread-safe on its own).
    Falls back to calling directly if the loop isn't known yet (e.g. a
    request handled before lifespan startup finished, or in tests that
    call broker functions without ever starting the app)."""
    if _LOOP is not None:
        _LOOP.call_soon_threadsafe(fn, *args)
    else:
        fn(*args)


def _fish_poll_loop() -> None:
    """Re-check the configured fish source(s) periodically and republish
    only when the window actually changed, so the dashboard's live feed
    panel updates on its own. Never raises - a transient collection
    failure just gets retried next tick."""
    last_fingerprint: str | None = None
    while not _STOP_EVENT.is_set():
        try:
            data, source = collect_fish_verbose()
            fingerprint = hashlib.sha256(
                json.dumps(data, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()
            if fingerprint != last_fingerprint:
                last_fingerprint = fingerprint
                _publish(data, source)
        except FishSourceError:
            pass  # no source available yet (e.g. vision.py not running) - routine, retry next tick
        except Exception as exc:  # never let a poll hiccup kill the background thread
            print(f"[fish-poll] unexpected error: {exc}", file=sys.stderr)
        _STOP_EVENT.wait(config.VISION_POLL_INTERVAL_S)


def _capture_live_audio() -> tuple[dict, str]:
    """Block for one ESP32 audio window (one-shot open/read/close). Raises
    SerialReaderError if the port is unavailable. Shared by the encrypt-time
    fallback in _resolve_audio() and (via a persistent connection instead of
    reopening each window) the audio poll loop below."""
    cfg = SerialReaderConfig(
        port=config.AUDIO_SERIAL_PORT,
        baud_rate=config.AUDIO_BAUD_RATE,
        window_duration_s=config.AUDIO_WINDOW_DURATION_S,
        min_readings=config.AUDIO_MIN_READINGS,
    )
    with _AUDIO_PORT_LOCK:
        with ESP32SerialReader(cfg) as reader:
            readings = reader.read_window()
    return to_audio_observation(readings, window_duration_s=cfg.window_duration_s), f"live:{config.AUDIO_SERIAL_PORT}"


def _audio_poll_loop() -> None:
    """Hold the ESP32 serial port open and continuously republish windows
    to the dashboard's live mic panel. Only started if config.AUDIO_SERIAL_PORT
    is set. Keeps ONE connection open across windows (rather than
    open/read/close per window like _capture_live_audio) so it doesn't
    repeatedly toggle the USB-serial line and risk tripping the ESP32's
    auto-reset circuit every poll tick."""
    cfg = SerialReaderConfig(
        port=config.AUDIO_SERIAL_PORT,
        baud_rate=config.AUDIO_BAUD_RATE,
        window_duration_s=config.AUDIO_WINDOW_DURATION_S,
        min_readings=config.AUDIO_MIN_READINGS,
    )
    while not _STOP_EVENT.is_set():
        try:
            with _AUDIO_PORT_LOCK:
                with ESP32SerialReader(cfg) as reader:
                    while not _STOP_EVENT.is_set():
                        readings = reader.read_window()
                        data = to_audio_observation(readings, window_duration_s=cfg.window_duration_s)
                        _publish_audio(data, f"live:{config.AUDIO_SERIAL_PORT}")
        except SerialReaderError as exc:
            print(f"[audio-poll] {exc}; retrying in 2s", file=sys.stderr)
            _STOP_EVENT.wait(2.0)
        except Exception as exc:
            print(f"[audio-poll] unexpected error: {exc}", file=sys.stderr)
            _STOP_EVENT.wait(2.0)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _LOOP
    _LOOP = asyncio.get_running_loop()
    _STOP_EVENT.clear()
    threads = [threading.Thread(target=_fish_poll_loop, name="fish-poll", daemon=True)]
    if config.AUDIO_SERIAL_PORT:
        threads.append(threading.Thread(target=_audio_poll_loop, name="audio-poll", daemon=True))
    for t in threads:
        t.start()
    try:
        yield
    finally:
        _STOP_EVENT.set()  # daemon threads; this just lets them exit their sleep promptly


app = FastAPI(title="FISHRAND", version=fishrand.__version__, lifespan=_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------------------------------------------------------------------------
# Live observation broker: latest validated window + SSE fan-out.
# --------------------------------------------------------------------------
_CURRENT: dict | None = None            # current validated observation window
_CURRENT_SOURCE: str | None = None
_CURRENT_AT: str | None = None
_sse_subscribers: set[asyncio.Queue] = set()
_subscribers_lock = threading.Lock()


def _publish(raw: dict, source: str) -> tuple[dict, str]:
    """Store + broadcast a new observation window. Returns (window, source).

    Safe to call from a background poller thread as well as a request
    handler (FastAPI runs sync `def` endpoints in a worker thread too) -
    the actual queue fan-out is marshalled onto the event loop via
    _schedule()/call_soon_threadsafe, since asyncio.Queue isn't safe to
    push into from an arbitrary thread otherwise."""
    global _CURRENT, _CURRENT_SOURCE, _CURRENT_AT
    data = validate_observations(_normalize_raw(raw))
    _CURRENT = data
    _CURRENT_SOURCE = source
    _CURRENT_AT = datetime.datetime.now(datetime.timezone.utc).isoformat()
    event = {
        "source": source,
        "received_at": _CURRENT_AT,
        "metadata": observation_stats(data).to_dict(),
    }

    def _broadcast() -> None:
        for queue in list(_sse_subscribers):
            queue.put_nowait({"event": "fish_update", "data": json.dumps(event)})

    _schedule(_broadcast)
    return data, source


async def _sse_broadcast() -> AsyncIterator[dict]:
    queue: asyncio.Queue = asyncio.Queue()
    with _subscribers_lock:
        _sse_subscribers.add(queue)
    try:
        if _CURRENT is not None:
            yield {
                "event": "fish_update",
                "data": json.dumps({
                    "source": _CURRENT_SOURCE,
                    "received_at": _CURRENT_AT,
                    "cached": True,
                    "metadata": observation_stats(_CURRENT).to_dict(),
                }),
            }
        while True:
            item = await queue.get()
            yield item
    finally:
        with _subscribers_lock:
            _sse_subscribers.discard(queue)


# --------------------------------------------------------------------------
# Live audio broker: latest validated ESP32 mic window + SSE fan-out.
# Mirrors the fish broker above; kept separate because a package can be
# GOOD-quality fish-only with no audio at all, so the two need independent
# "do we have one yet" states.
# --------------------------------------------------------------------------
_CURRENT_AUDIO: dict | None = None
_CURRENT_AUDIO_SOURCE: str | None = None
_CURRENT_AUDIO_AT: str | None = None
_audio_sse_subscribers: set[asyncio.Queue] = set()

# How long a cached audio window is still trusted for encryption before
# _resolve_audio() falls back to a fresh capture - a few window-lengths, so
# a briefly-slow poll tick doesn't force a redundant capture, but an
# actually-unplugged ESP32 doesn't get treated as "current" forever.
_AUDIO_STALE_AFTER_S = config.AUDIO_WINDOW_DURATION_S * 4


def _audio_stats(validated_audio: dict) -> dict:
    """Dashboard-only metadata for an audio window - not covered by
    fishrand.observe.observation_stats (that module only knows the fish
    'samples'/'frames' shapes, not the audio 'readings' shape)."""
    readings = validated_audio.get("readings", [])
    values = [r["value"] for r in readings]
    return {
        "derived_by": "audio_serial",
        "reading_count": len(values),
        "window_duration_s": validated_audio.get("window_duration_s"),
        "mean_level": round(statistics.fmean(values), 2) if values else None,
        "min_level": min(values) if values else None,
        "max_level": max(values) if values else None,
    }


def _publish_audio(raw: dict, source: str) -> tuple[dict, str]:
    """Store + broadcast a new ESP32 audio window. Returns (window, source)."""
    global _CURRENT_AUDIO, _CURRENT_AUDIO_SOURCE, _CURRENT_AUDIO_AT
    data = validate_observations(raw)
    _CURRENT_AUDIO = data
    _CURRENT_AUDIO_SOURCE = source
    _CURRENT_AUDIO_AT = datetime.datetime.now(datetime.timezone.utc).isoformat()
    event = {
        "source": source,
        "received_at": _CURRENT_AUDIO_AT,
        "metadata": _audio_stats(data),
    }

    def _broadcast() -> None:
        for queue in list(_audio_sse_subscribers):
            queue.put_nowait({"event": "audio_update", "data": json.dumps(event)})

    _schedule(_broadcast)
    return data, source


async def _audio_sse_broadcast() -> AsyncIterator[dict]:
    queue: asyncio.Queue = asyncio.Queue()
    with _subscribers_lock:
        _audio_sse_subscribers.add(queue)
    try:
        if _CURRENT_AUDIO is not None:
            yield {
                "event": "audio_update",
                "data": json.dumps({
                    "source": _CURRENT_AUDIO_SOURCE,
                    "received_at": _CURRENT_AUDIO_AT,
                    "cached": True,
                    "metadata": _audio_stats(_CURRENT_AUDIO),
                }),
            }
        while True:
            item = await queue.get()
            yield item
    finally:
        with _subscribers_lock:
            _audio_sse_subscribers.discard(queue)


# --------------------------------------------------------------------------
# pydantic request bodies
# --------------------------------------------------------------------------
class FishRequest(BaseModel):
    plaintext: str | None = None
    fish_json: dict | None = Field(default=None, description="optional override for tests")
    audio_json: dict | None = Field(default=None, description="optional audio override for tests")
    no_audio: bool = Field(default=False, description="skip ESP32 audio capture entirely")


class DecryptRequest(BaseModel):
    package: dict | None = None
    private_key_pem: str | None = Field(default=None, description="your RSA private key PEM")


class ObservationsRequest(BaseModel):
    observations: dict | list


class DiarySaveRequest(BaseModel):
    plaintext: str
    no_audio: bool = Field(default=False, description="skip ESP32 audio capture entirely")


class DiaryUnlockRequest(BaseModel):
    private_key_pem: str | None = Field(default=None, description="your RSA private key PEM")


class GenerateKeysRequest(BaseModel):
    force: bool = Field(default=False, description="regenerate even if a public key already exists")


# --------------------------------------------------------------------------
# key handling - public key on disk, private keys only ever in a request
# --------------------------------------------------------------------------
def _server_public_key():
    """Load the server's RSA public key, or 409 if none has been made yet."""
    if not config.PUBLIC_KEY_PATH.exists():
        raise HTTPException(
            status_code=409,
            detail="no encryption key yet — generate a keypair first (POST /api/keys/generate)",
        )
    return load_public_key(config.PUBLIC_KEY_PATH)


def _caller_private_key(private_key_pem: str | None):
    """Parse a caller-supplied private key PEM. Used in memory only - it is
    never written to disk, logged, or cached."""
    if not private_key_pem or not private_key_pem.strip():
        raise HTTPException(status_code=422, detail="private_key_pem is required to decrypt")
    try:
        return load_private_key_from_pem(private_key_pem)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# --------------------------------------------------------------------------
# endpoints
# --------------------------------------------------------------------------
@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "module": "fishrand", "version": fishrand.__version__,
            "fish_source": current_source()}


@app.get("/api/events")
async def events() -> EventSourceResponse:
    return EventSourceResponse(_sse_broadcast())


@app.post("/api/observations")
def push_observations(body: ObservationsRequest) -> dict:
    try:
        data, source = _publish(body.observations, "push:/api/observations")
    except SchemaError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"accepted": True, "source": source, "units": count_observations(data)}


@app.get("/api/observations/current")
def current_observations() -> dict:
    if _CURRENT is None:
        raise HTTPException(status_code=404, detail="no observations received yet")
    return {
        "source": _CURRENT_SOURCE,
        "received_at": _CURRENT_AT,
        "observations": _CURRENT,
        "metadata": observation_stats(_CURRENT).to_dict(),
    }


@app.get("/api/audio/events")
async def audio_events() -> EventSourceResponse:
    return EventSourceResponse(_audio_sse_broadcast())


@app.get("/api/audio/current")
def current_audio() -> dict:
    if _CURRENT_AUDIO is None:
        raise HTTPException(status_code=404, detail="no audio received yet")
    return {
        "source": _CURRENT_AUDIO_SOURCE,
        "received_at": _CURRENT_AUDIO_AT,
        "observations": _CURRENT_AUDIO,
        "metadata": _audio_stats(_CURRENT_AUDIO),
    }


def _sse_from_events(events: list[dict], final: dict) -> EventSourceResponse:
    async def gen() -> AsyncIterator[dict]:
        for event in events:
            yield {"event": "pipe", "data": json.dumps(event)}
        yield {"event": "done", "data": json.dumps(final)}
        yield {"event": "close", "data": json.dumps("closed")}

    return EventSourceResponse(gen())


def _resolve_fish(fish_json: dict | None) -> tuple[dict, str]:
    """Priority: explicit override -> live/pushed window -> collector.

    Shared by /api/encrypt and /api/diary/save so both always agree on
    which fish window "the current one" means. Raises FishSourceError if
    every source fails (only reachable via the collector fallthrough).
    """
    if fish_json is not None:
        return fish_json, "override:request"
    if _CURRENT is not None:
        # Use the live/pushed window the dashboard is already displaying
        # (via /api/observations + SSE), so "what you see" is guaranteed
        # to be "what gets encrypted" instead of silently re-collecting a
        # possibly different window from the configured sources.
        return _CURRENT, _CURRENT_SOURCE or "live:pushed"
    return collect_fish_verbose()


def _resolve_audio(no_audio: bool, audio_json: dict | None) -> tuple[dict | None, str]:
    """Resolve the audio window for this encryption, mirroring _resolve_fish.

    Priority: explicit override -> fresh cached window from the background
    audio poller (if one is running and recent) -> a fresh one-shot live
    ESP32 capture (if configured) -> None. Preferring the cache means an
    encrypt call doesn't have to block for a whole new window when the
    poller already has one seconds old - same "what you see [on the
    dashboard] is what gets encrypted" guarantee _resolve_fish() gives for
    fish. Never raises - an unavailable/unconfigured ESP32 just means no
    audio for this session, and encrypt_with_observation() turns that into
    a clear error only if the fish quality actually required audio.
    """
    if audio_json is not None:
        return audio_json, "override:request"
    if no_audio:
        return None, "disabled:no_audio"
    if _CURRENT_AUDIO is not None and _CURRENT_AUDIO_AT is not None:
        received = datetime.datetime.fromisoformat(_CURRENT_AUDIO_AT)
        age_s = (datetime.datetime.now(datetime.timezone.utc) - received).total_seconds()
        if age_s <= _AUDIO_STALE_AFTER_S:
            return _CURRENT_AUDIO, _CURRENT_AUDIO_SOURCE or "live:cached"
    if not config.AUDIO_SERIAL_PORT:
        return None, "unconfigured"
    try:
        return _capture_live_audio()
    except SerialReaderError as exc:
        return None, f"unavailable:{exc}"


def _run_encrypt(
    fish_data: dict, source: str, plaintext: str, public_key: Any, audio_data: dict | None = None,
    audio_source: str = "unconfigured",
) -> tuple[list[dict], dict]:
    """Shared encrypt body for /api/encrypt and /api/diary/save.

    Returns (pipeline_events, package). Raises SchemaError/ValueError on
    invalid fish data or plaintext (caller maps to a 422) - including when
    fish quality requires audio that wasn't available.
    """
    events: list[dict] = [{
        "step": "collect",
        "status": "ok",
        "detail": {"source": source, "units": count_observations(fish_data), "audio_source": audio_source},
    }]
    package = encrypt_with_observation(
        fish_data,
        plaintext,
        public_key=public_key,
        audio_observations=audio_data,
        emit=lambda *a: events.append(_to_event(a)),
    )
    package["metadata"]["fish_source"] = source
    return events, package


def _run_decrypt(package: dict, private_key: Any) -> tuple[list[dict], dict]:
    """Shared decrypt body for /api/decrypt and /api/diary/unlock.

    Returns (pipeline_events, final_status_dict) — final is either
    {"status": "decrypted", "plaintext": ...} or {"status": "rejected",
    "reason": ...}; never raises AuthenticationFailure (converted to the
    rejected status so the SSE stream always completes cleanly). Only the
    RSA private key is needed - no fish/audio window, regardless of which
    observation_mode encrypted the package. May raise ValueError for the
    caller to map to a 422 (malformed package).
    """
    events: list[dict] = [{
        "step": "collect",
        "status": "ok",
        "detail": {"observation_mode": package.get("metadata", {}).get("observation_mode")},
    }]
    try:
        plaintext = decrypt_rsa_hybrid_package(
            package,
            private_key=private_key,
            emit=lambda *a: events.append(_to_event(a)),
        )
    except AuthenticationFailure as exc:
        events.append({
            "step": "aes_gcm",
            "status": "error",
            "detail": {"reason": "AUTHENTICATION FAILED", "message": str(exc)},
        })
        return events, {"status": "rejected", "reason": str(exc)}
    return events, {"status": "decrypted", "plaintext": plaintext.decode("utf-8")}


@app.post("/api/keys/generate")
def generate_keys(body: GenerateKeysRequest) -> Response:
    """Generate a fresh RSA-3072 keypair. Stores ONLY the public key on
    disk; the private key PEM is returned directly in the response body
    (never logged, cached, or written server-side) so the browser can
    download it immediately as private_key.pem.

    409 if a public key already exists and `force` wasn't passed -
    regenerating orphans any diary already encrypted with the old key.
    """
    if config.PUBLIC_KEY_PATH.exists() and not body.force:
        raise HTTPException(
            status_code=409,
            detail="a public key already exists — pass force=true to regenerate "
                   "(this orphans any diary already encrypted with the old key)",
        )

    private_key, public_key = generate_keypair()
    config.PUBLIC_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(config.PUBLIC_KEY_PATH.parent, 0o700)
    config.PUBLIC_KEY_PATH.write_bytes(serialize_public_key(public_key))
    os.chmod(config.PUBLIC_KEY_PATH, 0o644)  # public key: fine to be world-readable

    private_pem = serialize_private_key(private_key, None)
    return Response(
        content=private_pem,
        media_type="application/x-pem-file",
        headers={"Content-Disposition": 'attachment; filename="private_key.pem"'},
    )


@app.post("/api/encrypt")
def encrypt_endpoint(body: FishRequest) -> EventSourceResponse:
    if body.plaintext is None or body.plaintext == "":
        raise HTTPException(status_code=422, detail="plaintext is required")

    public_key = _server_public_key()
    try:
        fish_data, source = _resolve_fish(body.fish_json)
    except FishSourceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    audio_data, audio_source = _resolve_audio(body.no_audio, body.audio_json)

    try:
        events, package = _run_encrypt(fish_data, source, body.plaintext, public_key, audio_data, audio_source)
    except (SchemaError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return _sse_from_events(events, {"status": "encrypted", "package": package})


@app.post("/api/decrypt")
def decrypt_endpoint(body: DecryptRequest) -> EventSourceResponse:
    if body.package is None:
        raise HTTPException(status_code=422, detail="package is required")

    private_key = _caller_private_key(body.private_key_pem)
    try:
        events, final = _run_decrypt(body.package, private_key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _sse_from_events(events, final)


def _diary_saved_at() -> str | None:
    if not config.DIARY_PATH.exists():
        return None
    ts = config.DIARY_PATH.stat().st_mtime
    return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).isoformat()


def _read_diary_package() -> dict:
    """Read the stored diary package in its SERIALIZED (base64-string)
    form — the same shape /api/encrypt returns and /api/decrypt accepts.

    NOT fishrand.package.load_package(): that decodes straight to raw
    bytes fields, which is what decrypt_rsa_hybrid_package() wants
    internally but can't be JSON-serialized back to the browser (this is
    exactly what GET /api/diary needs to hand over as-is).
    """
    return json.loads(config.DIARY_PATH.read_text(encoding="utf-8"))


@app.get("/api/diary")
def diary_state() -> dict:
    """State of the diary app's one persistent document. Ciphertext +
    public metadata only — safe to return to anyone, same trust model as
    handing someone a .pkg file. `encryption_key_exists` tells the
    frontend whether to offer "generate a key" or "load your key"."""
    key_exists = config.PUBLIC_KEY_PATH.exists()
    if not config.DIARY_PATH.exists():
        return {"exists": False, "encryption_key_exists": key_exists}
    try:
        package = _read_diary_package()
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=500, detail=f"stored diary is unreadable: {exc}") from exc
    return {
        "exists": True,
        "package": package,
        "saved_at": _diary_saved_at(),
        "encryption_key_exists": key_exists,
    }


@app.post("/api/diary/save")
def diary_save(body: DiarySaveRequest) -> EventSourceResponse:
    """Encrypt body.plaintext (same fish-collection + crypto path as
    /api/encrypt) and OVERWRITE the one persistent document on disk. No
    secret needed client-side to encrypt: anyone holding the public key
    can - that's the point of the RSA split."""
    if not body.plaintext:
        raise HTTPException(status_code=422, detail="plaintext is required")

    public_key = _server_public_key()
    try:
        fish_data, source = _resolve_fish(None)
    except FishSourceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    audio_data, audio_source = _resolve_audio(body.no_audio, None)

    try:
        events, package = _run_encrypt(fish_data, source, body.plaintext, public_key, audio_data, audio_source)
    except (SchemaError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    config.DIARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(config.DIARY_PATH.parent, 0o700)
    save_package(package, str(config.DIARY_PATH))  # save_package chmod 0o600s the file itself
    events.append({
        "step": "persist",
        "status": "ok",
        "detail": {"path": str(config.DIARY_PATH), "bytes": config.DIARY_PATH.stat().st_size},
    })
    return _sse_from_events(events, {"status": "encrypted", "package": package, "saved_at": _diary_saved_at()})


@app.post("/api/diary/unlock")
def diary_unlock(body: DiaryUnlockRequest) -> EventSourceResponse:
    """Decrypt the one persistent document in place. Only the caller's RSA
    private key is used, in memory, for this one request - never written
    to disk, logged, or cached."""
    if not config.DIARY_PATH.exists():
        raise HTTPException(status_code=404, detail="no diary saved on this PC yet")
    try:
        package = _read_diary_package()
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=500, detail=f"stored diary is unreadable: {exc}") from exc

    private_key = _caller_private_key(body.private_key_pem)
    try:
        events, final = _run_decrypt(package, private_key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _sse_from_events(events, final)


def _to_event(args: tuple) -> dict:
    step, status, duration_ms, detail = args
    event: dict[str, Any] = {"step": step, "status": status, "detail": detail or {}}
    if duration_ms is not None:
        event["duration_ms"] = round(duration_ms, 2)
    return event


__all__ = ["app"]