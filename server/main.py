"""FISHRAND server - FastAPI backend streaming pipeline events via SSE.

The dashboard triggers encryption; this server collects the fish
observations itself (see server.collector) at encrypt time.

Endpoints:
    GET  /api/health           liveness + version
    GET  /api/events           persistent SSE stream: fish_update events
    POST /api/observations     push channel for the vision engine's output
    GET  /api/observations/current
    POST /api/encrypt          body {plaintext, code?}; server collects the
                               fish, binds it into the package (SSE). When a
                               universal USB code is supplied a v2 package is
                               produced (no secret embedded).
    POST /api/decrypt          body {package, code?}; uses the fish bound in
                               the package, verifies GCM tag (SSE). v2
                               packages require the USB code.
    GET  /api/diary            current state of the diary app's ONE
                               persistent encrypted document on this PC
                               (ciphertext + metadata only - never plaintext).
    POST /api/diary/save       body {plaintext, code?}; encrypts (same path
                               as /api/encrypt) and OVERWRITES the stored
                               document on disk (SSE).
    POST /api/diary/unlock     body {code?}; decrypts the stored document
                               in place using its own bound fish window (SSE).

Run:  uvicorn server.main:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import datetime
import json
import pathlib
import threading
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

import fishrand
from fishrand import (
    AuthenticationFailure,
    decrypt_package,
    encrypt_with_fish_entropy,
)
from fishrand.observe import count_observations, observation_stats
from fishrand.package import save_package
from fishrand.schema import SchemaError, validate_observations

from . import config
from .collector import FishSourceError, collect_fish_verbose, current_source, _normalize_raw

app = FastAPI(title="FISHRAND", version=fishrand.__version__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
    code: str | None = Field(default=None, description="universal USB code (hex); omit for v1 demo")


class DecryptRequest(BaseModel):
    package: dict | None = None
    fish_json: dict | None = Field(default=None, description="optional override for tests")
    code: str | None = Field(default=None, description="universal USB code (hex) for v2 packages")


class ObservationsRequest(BaseModel):
    observations: dict | list


class DiarySaveRequest(BaseModel):
    plaintext: str
    code: str | None = Field(default=None, description="universal USB code (hex); omit to rely on FISHRAND_USB_CODE")


class DiaryUnlockRequest(BaseModel):
    code: str | None = Field(default=None, description="universal USB code (hex); omit to rely on FISHRAND_USB_CODE")


# --------------------------------------------------------------------------
# code resolution
# --------------------------------------------------------------------------
def _configured_code() -> str | None:
    """Return the universal USB code text, or None if no code is set."""
    path = config.USB_CODE
    if not path:
        return None
    p = pathlib.Path(path)
    if p.exists():
        return p.read_text(encoding="utf-8").strip() or None
    return None


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


def _run_encrypt(fish_data: dict, source: str, plaintext: str, code: str | None) -> tuple[list[dict], dict]:
    """Shared encrypt body for /api/encrypt and /api/diary/save.

    Returns (pipeline_events, package). Raises SchemaError/ValueError on
    invalid fish data or plaintext (caller maps to a 422).
    """
    events: list[dict] = [{
        "step": "collect",
        "status": "ok",
        "detail": {"source": source, "units": count_observations(fish_data)},
    }]
    package = encrypt_with_fish_entropy(
        fish_data,
        plaintext,
        session_code=code,
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


@app.post("/api/encrypt")
def encrypt_endpoint(body: FishRequest) -> EventSourceResponse:
    if body.plaintext is None or body.plaintext == "":
        raise HTTPException(status_code=422, detail="plaintext is required")

    try:
        fish_data, source = _resolve_fish(body.fish_json)
    except FishSourceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    code = body.code or _configured_code()
    try:
        events, package = _run_encrypt(fish_data, source, body.plaintext, code)
    except (SchemaError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return _sse_from_events(events, {"status": "encrypted", "package": package})


@app.post("/api/decrypt")
def decrypt_endpoint(body: DecryptRequest) -> EventSourceResponse:
    if body.package is None:
        raise HTTPException(status_code=422, detail="package is required")

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

    code = body.code or _configured_code()
    try:
        events, package = _run_encrypt(fish_data, source, body.plaintext, code)
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

    bound_data = package.get("metadata", {}).get("fish_observations")
    if bound_data is None:
        raise HTTPException(
            status_code=422,
            detail="stored diary has no bound fish_observations (corrupt, or saved by an older client)",
        )
    fish_source = package.get("metadata", {}).get("fish_source", "package")

    code = body.code or _configured_code()
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