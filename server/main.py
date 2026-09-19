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
    GET  /api/diary/entries    every stored diary entry: ciphertext package +
                               plaintext flags (pinned/archived/deleted_at).
    POST /api/diary/entries    body {plaintext, drive_serial}; makes a FRESH
                               RSA-3072 keypair for this one entry, encrypts,
                               stores the package (with drive_serial baked
                               into its metadata as required_drive_serial),
                               and returns private_key_pem in the response
                               (SSE) — never saved server-side, so the caller
                               must keep it (e.g. on that drive) or this
                               entry is unrecoverable. Decrypting later also
                               requires that exact physical drive present
                               (fishrand/drive_detect.py) - a leaked key file
                               alone is not enough.
    PATCH  /api/diary/entries/{id}  body {pinned?, archived?, deleted?}
    DELETE /api/diary/entries/{id}  purge an entry that is already in the bin.
    GET  /api/vault/drives     currently mounted physical drives with a
                               readable hardware serial - candidates for
                               POST /api/diary/entries' drive_serial. Checked
                               fresh every call, never polled.
    GET  /api/metrics           snapshot: fish activity history, quality-tier
                               counts, encrypt/decrypt rate, HKDF + AES/RSA
                               wrap latency percentiles (server/metrics.py).
    GET  /api/metrics/events   persistent SSE stream: metrics_update, pushed
                               every config.METRICS_BROADCAST_INTERVAL_S.
    GET  /api/vault/status     current {unlocked, updated_at} - see note below.
    POST /api/vault/status     body {unlocked}; the diary app calls this on
                               lock()/unlock() so OTHER apps (the dashboard)
                               can show vault state too. Cosmetic/best-effort
                               only - a plain boolean, never the private key
                               or any decrypted content, and not something
                               the server verifies or relies on for anything
                               security-relevant (unlocking still always
                               requires the caller's own private key PEM,
                               per this file's security model above).
    GET  /api/vault/events      persistent SSE stream: vault_update events.
    GET  /api/vision/snapshot  latest camera frame as one JPEG, written by
                               vision.py to config.VISION_FRAME_PATH (404 if
                               vision.py isn't running / has no display).
    GET  /api/vision/stream    the same frame, as a live MJPEG
                               (multipart/x-mixed-replace) stream for an
                               <img> tag - server/main.py just re-reads that
                               one file on a timer; no camera access here.

LIVE DASHBOARD FEEDS:
    Two daemon background threads (started in the FastAPI lifespan below)
    keep the dashboard's "live" panels populated without any user action:
      - fish poller: re-checks the configured fish source(s) every
        config.VISION_POLL_INTERVAL_S seconds and republishes to the same
        broker/SSE stream a manual POST /api/observations would use. Also
        feeds server/metrics.py's fish-activity/quality-tier history.
      - audio poller: only runs if config.AUDIO_SERIAL_PORT is configured;
        holds the ESP32 serial port open and continuously republishes each
        Sound_Level window to its own broker/SSE stream.
    Both are display-only conveniences - _resolve_fish()/_resolve_audio()
    (used by actual encryption) simply read whatever these pollers last
    published, same as before a manual push, so encryption behaviour is
    unchanged by their presence. A third background task rebroadcasts the
    /api/metrics snapshot on the same kind of timer.

Run:  uvicorn server.main:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import os
import re
import shutil
import statistics
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Iterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

import fishrand
from fishrand import (
    AuthenticationFailure,
    decrypt_rsa_hybrid_package,
    encrypt_with_observation,
)
from fishrand import drive_detect
from fishrand.observe import count_observations, observation_stats
from fishrand.package import save_package
from fishrand.quality import classify_fish_quality
from fishrand.rsa_hybrid import (
    generate_keypair,
    load_private_key_from_pem,
    load_public_key,
    serialize_private_key,
    serialize_public_key,
)
from fishrand.schema import SchemaError, validate_observations

from . import config, metrics
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
    failure just gets retried next tick.

    Also feeds server/metrics.py's fish-activity/quality-tier history on
    EVERY tick (not just when the window changed), so the dashboard's
    activity line chart has a dense, regular time axis even while the
    tank is quiet."""
    last_fingerprint: str | None = None
    while not _STOP_EVENT.is_set():
        try:
            data, source = collect_fish_verbose()
            stats = observation_stats(data)
            quality = classify_fish_quality(
                data,
                stats,
                good_fish_count_min=config.FISH_QUALITY_GOOD_FISH_COUNT_MIN,
                good_activity_pct_min=config.FISH_QUALITY_GOOD_ACTIVITY_PCT_MIN,
                medium_fish_count_min=config.FISH_QUALITY_MEDIUM_FISH_COUNT_MIN,
                medium_activity_pct_min=config.FISH_QUALITY_MEDIUM_ACTIVITY_PCT_MIN,
            )
            metrics.record_fish_sample(stats.mean_activity_pct or 0.0, stats.max_fish_count or 0, quality)
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


async def _metrics_broadcast_loop() -> None:
    """Every config.METRICS_BROADCAST_INTERVAL_S: tick the ops rate-history
    series and push a fresh snapshot to every /api/metrics/events
    subscriber. Runs on the event loop itself (no blocking I/O), unlike
    the fish/audio pollers above which are plain threads."""
    while True:
        await asyncio.sleep(config.METRICS_BROADCAST_INTERVAL_S)
        metrics.tick_rate_history()
        snapshot = metrics.snapshot()
        for queue in list(_metrics_sse_subscribers):
            queue.put_nowait({"event": "metrics_update", "data": json.dumps(snapshot)})


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
    metrics_task = asyncio.create_task(_metrics_broadcast_loop())
    try:
        yield
    finally:
        _STOP_EVENT.set()  # daemon threads; this just lets them exit their sleep promptly
        metrics_task.cancel()


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
# Vault status broker: a cosmetic LOCKED/UNLOCKED flag the diary app pushes
# so OTHER apps (the dashboard) can show it too. Mirrors the audio broker
# above exactly, but carries nothing more than a boolean + timestamp - see
# this file's module docstring for why that's safe (never the key, never
# plaintext, and never trusted for anything security-relevant).
# --------------------------------------------------------------------------
_VAULT_UNLOCKED: bool = False
_VAULT_AT: str | None = None
_vault_sse_subscribers: set[asyncio.Queue] = set()


def _vault_snapshot() -> dict:
    return {"unlocked": _VAULT_UNLOCKED, "updated_at": _VAULT_AT}


def _publish_vault(unlocked: bool) -> dict:
    global _VAULT_UNLOCKED, _VAULT_AT
    _VAULT_UNLOCKED = unlocked
    _VAULT_AT = datetime.datetime.now(datetime.timezone.utc).isoformat()
    event = _vault_snapshot()

    def _broadcast() -> None:
        for queue in list(_vault_sse_subscribers):
            queue.put_nowait({"event": "vault_update", "data": json.dumps(event)})

    _schedule(_broadcast)
    return event


async def _vault_sse_broadcast() -> AsyncIterator[dict]:
    queue: asyncio.Queue = asyncio.Queue()
    with _subscribers_lock:
        _vault_sse_subscribers.add(queue)
    try:
        yield {"event": "vault_update", "data": json.dumps(_vault_snapshot())}
        while True:
            item = await queue.get()
            yield item
    finally:
        with _subscribers_lock:
            _vault_sse_subscribers.discard(queue)


# --------------------------------------------------------------------------
# Metrics broker: fan-out for /api/metrics/events. The samples themselves
# live in server/metrics.py; this is just the SSE subscriber set the
# _metrics_broadcast_loop background task (defined above, started in
# _lifespan) pushes snapshots into.
# --------------------------------------------------------------------------
_metrics_sse_subscribers: set[asyncio.Queue] = set()


async def _metrics_sse_broadcast() -> AsyncIterator[dict]:
    queue: asyncio.Queue = asyncio.Queue()
    with _subscribers_lock:
        _metrics_sse_subscribers.add(queue)
    try:
        yield {"event": "metrics_update", "data": json.dumps(metrics.snapshot())}
        while True:
            item = await queue.get()
            yield item
    finally:
        with _subscribers_lock:
            _metrics_sse_subscribers.discard(queue)


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


class CreateEntryRequest(DiarySaveRequest):
    drive_serial: str = Field(description="hardware serial of the drive this entry's key is being saved to")


class DiaryUnlockRequest(BaseModel):
    private_key_pem: str | None = Field(default=None, description="your RSA private key PEM")


class EntryFlagsRequest(BaseModel):
    pinned: bool | None = None
    archived: bool | None = None
    deleted: bool | None = Field(default=None, description="true moves to the bin, false restores")


class GenerateKeysRequest(BaseModel):
    force: bool = Field(default=False, description="regenerate even if a public key already exists")


class VaultStatusRequest(BaseModel):
    unlocked: bool = Field(description="the diary app's current lock state - never the key or any content")


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
    try:
        package = encrypt_with_observation(
            fish_data,
            plaintext,
            public_key=public_key,
            audio_observations=audio_data,
            emit=lambda *a: events.append(_to_event(a)),
        )
    except (SchemaError, ValueError):
        metrics.record_op("encrypt", ok=False)
        raise
    package["metadata"]["fish_source"] = source

    by_step = {e["step"]: e.get("duration_ms") for e in events}
    aes_ms, rsa_ms = by_step.get("aes_gcm"), by_step.get("rsa_wrap")
    wrap_ms = (aes_ms or 0) + (rsa_ms or 0) if (aes_ms is not None or rsa_ms is not None) else None
    metrics.record_encrypt_latencies(by_step.get("kdf"), wrap_ms)
    metrics.record_op("encrypt", ok=True)
    return events, package


def _run_decrypt(package: dict, private_key: Any) -> tuple[list[dict], dict]:
    """Shared decrypt body for /api/decrypt and /api/diary/unlock.

    Returns (pipeline_events, final_status_dict) — final is either
    {"status": "decrypted", "plaintext": ...} or {"status": "rejected",
    "reason": ..., "code": ...}; never raises AuthenticationFailure
    (converted to the rejected status so the SSE stream always completes
    cleanly). Only the RSA private key is needed - no fish/audio window,
    regardless of which observation_mode encrypted the package. May raise
    ValueError for the caller to map to a 422 (malformed package).

    Packages made with a required drive (server/main.py's create_entry,
    fishrand/drive_detect.py) additionally need that physical drive
    present - checked BEFORE any RSA/AES work, and given its own "code" so
    the caller can tell "wrong key" apart from "right key, missing drive."
    This is a server-side policy check, not cryptography: it gates this
    app's own /api/decrypt, not the underlying key math itself.
    """
    events: list[dict] = [{
        "step": "collect",
        "status": "ok",
        "detail": {"observation_mode": package.get("metadata", {}).get("observation_mode")},
    }]
    metadata = package.get("metadata", {}) or {}
    required_serial = metadata.get("required_drive_serial")
    if required_serial and not drive_detect.is_present(required_serial):
        label = metadata.get("required_drive_label", required_serial)
        events.append({
            "step": "drive",
            "status": "error",
            "detail": {"reason": "DRIVE MISSING", "message": f"insert the drive this entry needs: {label}"},
        })
        metrics.record_op("decrypt", ok=False)
        return events, {
            "status": "rejected", "code": "drive_missing",
            "reason": f"insert the drive this entry needs: {label}",
        }
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
        metrics.record_op("decrypt", ok=False)
        return events, {"status": "rejected", "code": "auth_failed", "reason": str(exc)}
    metrics.record_op("decrypt", ok=True)
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


# --------------------------------------------------------------------------
# multi-entry diary - one package per entry under config.ENTRIES_DIR
# --------------------------------------------------------------------------
# "legacy" is the pre-multi-entry data/diary.pkg, copied in on first list.
_ENTRY_ID_RE = re.compile(r"^(legacy|[0-9a-f]{32})$")
_DEFAULT_FLAGS = {"pinned": False, "archived": False, "deleted_at": None}


def _entry_paths(entry_id: str) -> tuple[Any, Any]:
    """(package path, flags path) for a validated id - the regex is what
    keeps a caller-supplied id from escaping ENTRIES_DIR."""
    if not _ENTRY_ID_RE.match(entry_id):
        raise HTTPException(status_code=422, detail="invalid entry id")
    return config.ENTRIES_DIR / f"{entry_id}.pkg", config.ENTRIES_DIR / f"{entry_id}.flags.json"


def _ensure_entries_dir() -> None:
    config.ENTRIES_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(config.ENTRIES_DIR, 0o700)


def _read_flags(flags_path) -> dict:
    """Plaintext sidecar: only pinned/archived/deleted_at, never content."""
    if not flags_path.exists():
        return dict(_DEFAULT_FLAGS)
    try:
        stored = json.loads(flags_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return dict(_DEFAULT_FLAGS)
    return {key: stored.get(key, default) for key, default in _DEFAULT_FLAGS.items()}


def _migrate_legacy_diary() -> None:
    """Copy (never move) the single-document diary in as entry "legacy",
    once. The original stays where /api/diary expects it."""
    legacy_pkg, _ = _entry_paths("legacy")
    purged_marker = config.ENTRIES_DIR / ".legacy-purged"
    if config.DIARY_PATH.exists() and not legacy_pkg.exists() and not purged_marker.exists():
        _ensure_entries_dir()
        shutil.copyfile(config.DIARY_PATH, legacy_pkg)
        os.chmod(legacy_pkg, 0o600)


@app.get("/api/diary/entries")
def list_entries() -> dict:
    """Every stored entry: ciphertext package + plaintext flags. Same trust
    model as GET /api/diary - nothing here needs or reveals a secret."""
    _migrate_legacy_diary()
    entries = []
    if config.ENTRIES_DIR.exists():
        for pkg_path in sorted(config.ENTRIES_DIR.glob("*.pkg")):
            entry_id = pkg_path.stem
            if not _ENTRY_ID_RE.match(entry_id):
                continue
            try:
                package = json.loads(pkg_path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue  # one unreadable file shouldn't hide the rest
            _, flags_path = _entry_paths(entry_id)
            entries.append({"id": entry_id, "package": package, "flags": _read_flags(flags_path)})
    entries.sort(key=lambda e: e["package"].get("metadata", {}).get("created_at", ""), reverse=True)
    return {"encryption_key_exists": config.PUBLIC_KEY_PATH.exists(), "entries": entries}


@app.get("/api/vault/drives")
def vault_drives() -> dict:
    """Currently mounted physical drives with a readable hardware serial -
    candidates for binding a new entry's key to. Checked fresh on every
    call, never cached or polled."""
    return {"drives": drive_detect.list_drives()}


@app.post("/api/diary/entries")
def create_entry(body: CreateEntryRequest) -> EventSourceResponse:
    """Encrypt body.plaintext under a FRESH RSA-3072 keypair made just for
    this one entry (not the shared PUBLIC_KEY_PATH keypair). The private
    key is returned in this one response and never written to disk,
    logged, or cached server-side — the caller is responsible for saving
    it (e.g. to a USB drive) or this entry can never be decrypted again.

    body.drive_serial is baked into the package's metadata: decrypting
    this entry later also requires that exact physical drive to be
    present (fishrand/drive_detect.py, checked in _run_decrypt), so a
    leaked key file alone is no longer enough."""
    if not body.plaintext:
        raise HTTPException(status_code=422, detail="plaintext is required")
    drives_by_serial = {d["serial"]: d for d in drive_detect.list_drives()}
    if body.drive_serial not in drives_by_serial:
        raise HTTPException(status_code=422, detail="that drive isn't currently mounted — pick one that's plugged in")

    private_key, public_key = generate_keypair()
    try:
        fish_data, source = _resolve_fish(None)
    except FishSourceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    audio_data, audio_source = _resolve_audio(body.no_audio, None)

    try:
        events, package = _run_encrypt(fish_data, source, body.plaintext, public_key, audio_data, audio_source)
    except (SchemaError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    package["metadata"]["required_drive_serial"] = body.drive_serial
    package["metadata"]["required_drive_label"] = drives_by_serial[body.drive_serial]["label"]

    entry_id = uuid.uuid4().hex
    pkg_path, _ = _entry_paths(entry_id)
    _ensure_entries_dir()
    save_package(package, str(pkg_path))  # save_package chmod 0o600s the file itself
    events.append({
        "step": "persist",
        "status": "ok",
        "detail": {"entry_id": entry_id, "bytes": pkg_path.stat().st_size},
    })
    private_pem = serialize_private_key(private_key, None).decode("utf-8")
    return _sse_from_events(events, {
        "status": "encrypted", "id": entry_id, "package": package, "flags": dict(_DEFAULT_FLAGS),
        "private_key_pem": private_pem,
    })


@app.patch("/api/diary/entries/{entry_id}")
def update_entry_flags(entry_id: str, body: EntryFlagsRequest) -> dict:
    pkg_path, flags_path = _entry_paths(entry_id)
    if not pkg_path.exists():
        raise HTTPException(status_code=404, detail="no such entry")
    flags = _read_flags(flags_path)
    if body.pinned is not None:
        flags["pinned"] = body.pinned
    if body.archived is not None:
        flags["archived"] = body.archived
    if body.deleted is True and not flags["deleted_at"]:
        flags["deleted_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    elif body.deleted is False:
        flags["deleted_at"] = None
    flags_path.write_text(json.dumps(flags), encoding="utf-8")
    os.chmod(flags_path, 0o600)
    return {"id": entry_id, "flags": flags}


@app.delete("/api/diary/entries/{entry_id}")
def purge_entry(entry_id: str) -> dict:
    """Permanently remove an entry - only once it's already in the bin."""
    pkg_path, flags_path = _entry_paths(entry_id)
    if not pkg_path.exists():
        raise HTTPException(status_code=404, detail="no such entry")
    if not _read_flags(flags_path)["deleted_at"]:
        raise HTTPException(status_code=409, detail="move the entry to the bin before deleting it forever")
    pkg_path.unlink()
    flags_path.unlink(missing_ok=True)
    if entry_id == "legacy":
        # Remember the purge so the next list doesn't copy the old diary
        # straight back in. data/diary.pkg itself is left untouched.
        (config.ENTRIES_DIR / ".legacy-purged").touch()
    return {"id": entry_id, "deleted": True}


# --------------------------------------------------------------------------
# dashboard metrics - see server/metrics.py; samples are fed in from
# _fish_poll_loop and _run_encrypt/_run_decrypt above.
# --------------------------------------------------------------------------
@app.get("/api/metrics")
def metrics_snapshot() -> dict:
    return metrics.snapshot()


@app.get("/api/metrics/events")
async def metrics_events() -> EventSourceResponse:
    return EventSourceResponse(_metrics_sse_broadcast())


# --------------------------------------------------------------------------
# vault status - cosmetic LOCKED/UNLOCKED relay; see _publish_vault above
# and this file's module docstring for what this is (and isn't) trusted for.
# --------------------------------------------------------------------------
@app.get("/api/vault/status")
def vault_status_current() -> dict:
    return _vault_snapshot()


@app.post("/api/vault/status")
def vault_status_update(body: VaultStatusRequest) -> dict:
    return _publish_vault(body.unlocked)


@app.get("/api/vault/events")
async def vault_status_events() -> EventSourceResponse:
    return EventSourceResponse(_vault_sse_broadcast())


# --------------------------------------------------------------------------
# vision camera preview - vision.py writes JPEG frames to
# config.VISION_FRAME_PATH; this server only ever reads that one file, it
# never touches a camera itself.
# --------------------------------------------------------------------------
@app.get("/api/vision/snapshot")
def vision_snapshot() -> Response:
    if not config.VISION_FRAME_PATH.exists():
        raise HTTPException(status_code=404, detail="no camera frame yet - is vision.py running with a display?")
    return Response(content=config.VISION_FRAME_PATH.read_bytes(), media_type="image/jpeg")


@app.get("/api/vision/stream")
def vision_stream() -> StreamingResponse:
    """Live-ish MJPEG feed for an <img> tag: re-reads the one frame file on
    a timer and yields it as a new multipart part each time. Not real
    per-frame video - it's exactly as fresh as vision.py's own
    FISHRAND_VISION_FRAME_INTERVAL_S snapshot cadence."""

    def _frames() -> Iterator[bytes]:
        boundary = b"--frame"
        while True:
            if config.VISION_FRAME_PATH.exists():
                try:
                    frame = config.VISION_FRAME_PATH.read_bytes()
                except OSError:
                    frame = None
                if frame:
                    yield (
                        boundary + b"\r\n"
                        b"Content-Type: image/jpeg\r\n"
                        b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n"
                    )
            time.sleep(config.VISION_STREAM_POLL_S)

    return StreamingResponse(_frames(), media_type="multipart/x-mixed-replace; boundary=frame")


def _to_event(args: tuple) -> dict:
    step, status, duration_ms, detail = args
    event: dict[str, Any] = {"step": step, "status": status, "detail": detail or {}}
    if duration_ms is not None:
        event["duration_ms"] = round(duration_ms, 2)
    return event


__all__ = ["app"]