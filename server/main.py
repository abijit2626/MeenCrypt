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

Run:  uvicorn server.main:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import threading
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
from .audio_serial import SerialReaderError
from .collector import FishSourceError, collect_fish_verbose, current_source, _normalize_raw

app = FastAPI(title="FISHRAND", version=fishrand.__version__)

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
    """Store + broadcast a new observation window. Returns (window, source)."""
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
    for queue in list(_sse_subscribers):
        queue.put_nowait({"event": "fish_update", "data": json.dumps(event)})
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

    Priority: explicit override -> live ESP32 capture (if configured) ->
    None. Never raises - an unavailable/unconfigured ESP32 just means no
    audio for this session, and encrypt_with_observation() turns that into
    a clear error only if the fish quality actually required audio.
    """
    if audio_json is not None:
        return audio_json, "override:request"
    if no_audio:
        return None, "disabled:no_audio"
    if not config.AUDIO_SERIAL_PORT:
        return None, "unconfigured"
    from .audio_serial import ESP32SerialReader, SerialReaderConfig, to_audio_observation

    cfg = SerialReaderConfig(
        port=config.AUDIO_SERIAL_PORT,
        baud_rate=config.AUDIO_BAUD_RATE,
        window_duration_s=config.AUDIO_WINDOW_DURATION_S,
        min_readings=config.AUDIO_MIN_READINGS,
    )
    try:
        with ESP32SerialReader(cfg) as reader:
            readings = reader.read_window()
    except SerialReaderError as exc:
        return None, f"unavailable:{exc}"
    return to_audio_observation(readings, window_duration_s=cfg.window_duration_s), f"live:{config.AUDIO_SERIAL_PORT}"


def _run_encrypt(
    fish_data: dict, source: str, plaintext: str, code: str | None, audio_data: dict | None = None,
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
    if code:
        # v5 (fish/audio observation-mode) never embeds a secret, so mode
        # selection only applies with a USB code; without one this stays
        # the untouched v1 self-contained demo path (audio doesn't apply).
        package = encrypt_with_observation(
            fish_data,
            plaintext,
            audio_observations=audio_data,
            session_code=code,
            emit=lambda *a: events.append(_to_event(a)),
        )
    else:
        package = encrypt_with_fish_entropy(
            fish_data,
            plaintext,
            session_code=None,
            emit=lambda *a: events.append(_to_event(a)),
        )
    # Bind the fish window into the package so decrypt uses the SAME fish.
    package["metadata"]["fish_observations"] = fish_data
    package["metadata"]["fish_source"] = source
    return events, package


def _run_decrypt(fish_data: dict, package: dict, source: str, bound: str, code: str | None) -> tuple[list[dict], dict]:
    """Shared decrypt body for /api/decrypt and /api/diary/unlock.

    Returns (pipeline_events, final_status_dict) — final is either
    {"status": "decrypted", "plaintext": ...} or {"status": "rejected",
    "reason": ...}; never raises AuthenticationFailure (converted to the
    rejected status so the SSE stream always completes cleanly). Raises
    SchemaError/ValueError on malformed fish data or package (caller maps
    those to a 422).
    """
    events: list[dict] = [{"step": "collect", "status": "ok", "detail": {"source": source, "bound": bound}}]
    try:
        plaintext = decrypt_package(
            fish_data,
            package,
            session_code=code,
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


def _run_decrypt_v5(
    package: dict, fish_data: dict | None, audio_data: dict | None, code: str | None
) -> tuple[list[dict], dict]:
    """Shared v5 (observation-mode) decrypt body for /api/decrypt and
    /api/diary/unlock. Mirrors _run_decrypt's contract: never raises
    AuthenticationFailure/ObservationModeMismatch (converted to a
    "rejected" status), may raise SchemaError/ValueError for the caller to
    map to a 422."""
    events: list[dict] = [{
        "step": "collect",
        "status": "ok",
        "detail": {
            "observation_mode": package.get("observation_mode"),
            "fish": fish_data is not None,
            "audio": audio_data is not None,
        },
    }]
    try:
        plaintext = decrypt_observation_package(
            package,
            fish_observations=fish_data,
            audio_observations=audio_data,
            session_code=code,
            emit=lambda *a: events.append(_to_event(a)),
        )
    except (AuthenticationFailure, ObservationModeMismatch) as exc:
        events.append({
            "step": "aes_gcm",
            "status": "error",
            "detail": {"reason": "REJECTED", "message": str(exc)},
        })
        return events, {"status": "rejected", "reason": str(exc)}
    return events, {"status": "decrypted", "plaintext": plaintext.decode("utf-8")}


@app.post("/api/encrypt")
def encrypt_endpoint(body: FishRequest) -> EventSourceResponse:
    if body.plaintext is None or body.plaintext == "":
        raise HTTPException(status_code=422, detail="plaintext is required")

    try:
        fish_data, source = _resolve_fish(body.fish_json)
    except FishSourceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    audio_data, audio_source = _resolve_audio(body.no_audio, body.audio_json)

    code = body.code or _configured_code()
    try:
        events, package = _run_encrypt(fish_data, source, body.plaintext, code, audio_data, audio_source)
    except (SchemaError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return _sse_from_events(events, {"status": "encrypted", "package": package})


@app.post("/api/decrypt")
def decrypt_endpoint(body: DecryptRequest) -> EventSourceResponse:
    if body.package is None:
        raise HTTPException(status_code=422, detail="package is required")

    if body.package.get("version") == 5:
        fish_data = body.fish_json if body.fish_json is not None else body.package.get("metadata", {}).get("fish_observations")
        audio_data = body.audio_json if body.audio_json is not None else body.package.get("metadata", {}).get("audio_observations")
        code = body.code or _configured_code()
        try:
            events, final = _run_decrypt_v5(body.package, fish_data, audio_data, code)
        except (SchemaError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _sse_from_events(events, final)

    if body.fish_json is not None:
        fish_data, fish_source, bound = body.fish_json, "override:request", "override"
    else:
        bound_data = body.package.get("metadata", {}).get("fish_observations")
        if bound_data is None:
            raise HTTPException(
                status_code=422,
                detail="package has no bound fish_observations (created by an older client); "
                       "resend fish_json to decrypt",
            )
        fish_data = bound_data
        fish_source = body.package.get("metadata", {}).get("fish_source", "package")
        bound = "package"

    code = body.code or _configured_code()
    try:
        events, final = _run_decrypt(fish_data, body.package, fish_source, bound, code)
    except (SchemaError, ValueError) as exc:
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
    bytes fields, which is what decrypt_package() wants internally but
    can't be JSON-serialized back to the browser (this is exactly what
    GET /api/diary needs to hand over as-is).
    """
    return json.loads(config.DIARY_PATH.read_text(encoding="utf-8"))


@app.get("/api/diary")
def diary_state() -> dict:
    """State of the diary app's one persistent document. Ciphertext +
    public metadata only — safe to return without any code, same trust
    model as handing someone a .pkg file."""
    if not config.DIARY_PATH.exists():
        return {"exists": False}
    try:
        package = _read_diary_package()
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=500, detail=f"stored diary is unreadable: {exc}") from exc
    return {"exists": True, "package": package, "saved_at": _diary_saved_at()}


@app.post("/api/diary/save")
def diary_save(body: DiarySaveRequest) -> EventSourceResponse:
    """Encrypt body.plaintext (same fish-collection + crypto path as
    /api/encrypt) and OVERWRITE the one persistent document on disk."""
    if not body.plaintext:
        raise HTTPException(status_code=422, detail="plaintext is required")

    try:
        fish_data, source = _resolve_fish(None)
    except FishSourceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    audio_data, audio_source = _resolve_audio(body.no_audio, None)

    code = body.code or _configured_code()
    try:
        events, package = _run_encrypt(fish_data, source, body.plaintext, code, audio_data, audio_source)
    except (SchemaError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    config.DIARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    save_package(package, config.DIARY_PATH)
    events.append({
        "step": "persist",
        "status": "ok",
        "detail": {"path": str(config.DIARY_PATH), "bytes": config.DIARY_PATH.stat().st_size},
    })
    return _sse_from_events(events, {"status": "encrypted", "package": package, "saved_at": _diary_saved_at()})


@app.post("/api/diary/unlock")
def diary_unlock(body: DiaryUnlockRequest) -> EventSourceResponse:
    """Decrypt the one persistent document in place, using its own bound
    fish window (exactly like /api/decrypt does for an uploaded package)."""
    if not config.DIARY_PATH.exists():
        raise HTTPException(status_code=404, detail="no diary saved on this PC yet")
    try:
        package = _read_diary_package()
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=500, detail=f"stored diary is unreadable: {exc}") from exc

    code = body.code or _configured_code()

    if package.get("version") == 5:
        fish_data = package.get("metadata", {}).get("fish_observations")
        audio_data = package.get("metadata", {}).get("audio_observations")
        try:
            events, final = _run_decrypt_v5(package, fish_data, audio_data, code)
        except (SchemaError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _sse_from_events(events, final)

    bound_data = package.get("metadata", {}).get("fish_observations")
    if bound_data is None:
        raise HTTPException(
            status_code=422,
            detail="stored diary has no bound fish_observations (corrupt, or saved by an older client)",
        )
    fish_source = package.get("metadata", {}).get("fish_source", "package")

    try:
        events, final = _run_decrypt(bound_data, package, fish_source, "package", code)
    except (SchemaError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _sse_from_events(events, final)


def _to_event(args: tuple) -> dict:
    step, status, duration_ms, detail = args
    event: dict[str, Any] = {"step": step, "status": status, "detail": detail or {}}
    if duration_ms is not None:
        event["duration_ms"] = round(duration_ms, 2)
    return event


__all__ = ["app"]