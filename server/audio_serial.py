"""ESP32 + INMP441 serial audio reader (hardware I/O only - no crypto here).

Reads "Sound_Level:<int>" lines from an ESP32 that is ALREADY streaming
them over USB serial at 115200 baud (the firmware and its I2S wiring -
WS=GPIO14, SCK=GPIO15, SD=GPIO32, 16000 Hz sample rate - are out of scope
and untouched by this module). This module ONLY reads the existing serial
stream; it never talks to the microphone directly and is never imported by
fishrand's pure-crypto modules (crypto.py/schema.py/entropy.py/mixing.py).

IMPORTANT: the Arduino IDE Serial Monitor (or any other program) holding
the port open will block pyserial from opening the same port - close the
Serial Monitor before running anything in this module against that port.

This lives in server/ (not fishrand/) for the same reason server/collector.py
does: it is a real-world I/O source feeding validated observations into the
crypto pipeline, not part of the pipeline's cryptographic math itself.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from fishrand.schema import AUDIO_SCHEMA_VERSION, AUDIO_SOURCE_IDENTIFIER

DEFAULT_BAUD_RATE = 115200
LINE_PREFIX = "Sound_Level:"

# The ESP32 firmware's Sound_Level formula is opaque (out of scope to
# redesign) - these are deliberately generous, named bounds that only
# reject clearly impossible values, not a tight guess around the sample
# values quoted in the integration spec (~11920-13102).
SOUND_LEVEL_MIN = 0
SOUND_LEVEL_MAX = 1_000_000

DEFAULT_READ_TIMEOUT_S = 1.0
DEFAULT_WINDOW_DURATION_S = 5.0   # matches vision.py's JSON_LOG_INTERVAL (one fish frame)
DEFAULT_MIN_READINGS = 3          # floor to reject a suspiciously sparse window


class SerialReaderError(RuntimeError):
    """Base error for ESP32 serial I/O - a hardware/transport problem, NOT
    a cryptographic error. Deliberately does NOT subclass fishrand's
    FishrandError: a disconnected USB cable is not a cryptographic event,
    and conflating the two hierarchies would force every `except
    FishrandError` call site to also reason about hardware faults."""


class DeviceNotFoundError(SerialReaderError):
    """The configured serial port does not exist (no ESP32 connected, or
    the wrong device path was given)."""


class DevicePermissionError(SerialReaderError):
    """The serial port exists but could not be opened - permissions, or
    another process (e.g. the Arduino IDE Serial Monitor) already has it
    open."""


class DeviceDisconnectedError(SerialReaderError):
    """The serial connection dropped mid-read (device unplugged, USB reset)."""


class MalformedLineError(SerialReaderError):
    """A recognized 'Sound_Level:' line failed to parse: non-numeric
    payload, or a value outside [SOUND_LEVEL_MIN, SOUND_LEVEL_MAX]."""


class InsufficientReadingsError(SerialReaderError):
    """Fewer than min_readings valid Sound_Level lines were collected
    within the configured window duration."""


@dataclass(frozen=True)
class AudioReading:
    value: int
    offset_s: float  # seconds since the read_window() call started


@dataclass
class SerialReaderConfig:
    port: str  # e.g. "/dev/ttyUSB0" or "/dev/ttyACM0" - REQUIRED, never guessed/hardcoded
    baud_rate: int = DEFAULT_BAUD_RATE
    read_timeout_s: float = DEFAULT_READ_TIMEOUT_S
    window_duration_s: float = DEFAULT_WINDOW_DURATION_S
    min_readings: int = DEFAULT_MIN_READINGS


class ESP32SerialReader:
    """Opens a serial port, reads Sound_Level lines, closes cleanly.

    Testable via dependency injection: pass `serial_factory` (a callable
    (port, baudrate, timeout) -> an object with .readline()/.close(), such
    as pyserial's `serial.Serial`) so unit tests can inject a fake
    in-memory serial object without any real ESP32 attached. Defaults to
    `serial.Serial` (imported lazily so importing this module never
    requires pyserial to be installed - only actually opening a port does).
    """

    def __init__(self, config: SerialReaderConfig, *, serial_factory=None) -> None:
        self._config = config
        self._serial_factory = serial_factory or self._default_serial_factory
        self._conn = None

    @staticmethod
    def _default_serial_factory(port: str, baud_rate: int, timeout: float):
        import serial as pyserial

        return pyserial.Serial(port=port, baudrate=baud_rate, timeout=timeout)

    def __enter__(self) -> "ESP32SerialReader":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def open(self) -> None:
        """Open the configured serial port.

        Raises DeviceNotFoundError if the port path doesn't exist, or
        DevicePermissionError if it exists but couldn't be opened
        (permissions, or another process - e.g. Arduino Serial Monitor -
        already holds it open).
        """
        try:
            self._conn = self._serial_factory(
                self._config.port, self._config.baud_rate, self._config.read_timeout_s
            )
        except FileNotFoundError as exc:
            raise DeviceNotFoundError(
                f"serial port {self._config.port!r} does not exist - is the ESP32 connected?"
            ) from exc
        except PermissionError as exc:
            raise DevicePermissionError(
                f"permission denied opening {self._config.port!r}"
            ) from exc
        except Exception as exc:  # pyserial raises serial.SerialException for both cases above too
            message = str(exc).lower()
            if "no such file" in message or "cannot find" in message:
                raise DeviceNotFoundError(
                    f"serial port {self._config.port!r} does not exist - is the ESP32 connected?"
                ) from exc
            if "permission" in message or "access is denied" in message or "could not exclusively lock" in message:
                raise DevicePermissionError(
                    f"could not open {self._config.port!r} - permission denied, or another "
                    "program (e.g. the Arduino IDE Serial Monitor) already has it open. "
                    "Close the Arduino Serial Monitor and try again."
                ) from exc
            raise DevicePermissionError(f"could not open {self._config.port!r}: {exc}") from exc

    def close(self) -> None:
        """Close the port; safe to call multiple times."""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    @staticmethod
    def parse_line(raw: bytes) -> int | None:
        """Decode one raw serial line and extract a Sound_Level value.

        Returns None for anything that isn't a recognized Sound_Level line
        (empty, unrelated text, undecodable bytes) - these are ignored, not
        errors. Raises MalformedLineError for a line that DOES match the
        "Sound_Level:" prefix but whose payload is invalid (non-numeric,
        negative, or larger than SOUND_LEVEL_MAX).
        """
        try:
            text = raw.decode("utf-8", errors="replace").strip()
        except AttributeError:
            text = str(raw).strip()
        if not text:
            return None
        if not text.startswith(LINE_PREFIX):
            return None
        payload = text[len(LINE_PREFIX):].strip()
        try:
            value = int(payload)
        except ValueError as exc:
            raise MalformedLineError(f"Sound_Level line has a non-numeric value: {payload!r}") from exc
        if not SOUND_LEVEL_MIN <= value <= SOUND_LEVEL_MAX:
            raise MalformedLineError(
                f"Sound_Level value {value} outside [{SOUND_LEVEL_MIN}, {SOUND_LEVEL_MAX}]"
            )
        return value

    def read_window(self) -> list[AudioReading]:
        """Collect Sound_Level readings for config.window_duration_s of
        wall-clock time.

        Uses elapsed time as the window boundary (not a fixed reading
        count) since the firmware's print rate isn't specified and may
        vary. Malformed lines are skipped with a warning-level ignore
        (they don't abort the window - a single garbled line shouldn't
        waste an otherwise-good capture), but a disconnect always aborts
        immediately. Raises InsufficientReadingsError if fewer than
        config.min_readings valid readings were collected in the window.
        """
        if self._conn is None:
            raise SerialReaderError("read_window() called before open() (or after close())")

        readings: list[AudioReading] = []
        start = time.monotonic()
        deadline = start + self._config.window_duration_s
        while time.monotonic() < deadline:
            try:
                raw = self._conn.readline()
            except Exception as exc:
                raise DeviceDisconnectedError(f"serial read failed: {exc}") from exc
            if raw == b"" and getattr(self._conn, "in_waiting", None) == 0:
                # A timed-out read with no data pending is normal (nothing
                # printed this tick) - keep waiting until the deadline.
                continue
            try:
                value = self.parse_line(raw)
            except MalformedLineError:
                continue
            if value is None:
                continue
            readings.append(AudioReading(value=value, offset_s=time.monotonic() - start))

        if len(readings) < self._config.min_readings:
            raise InsufficientReadingsError(
                f"only {len(readings)} valid Sound_Level reading(s) in "
                f"{self._config.window_duration_s}s (need >= {self._config.min_readings}); "
                "check the ESP32 is connected, the baud rate is 115200, and the "
                "Arduino Serial Monitor is closed"
            )
        return readings


def to_audio_observation(readings: list[AudioReading], *, window_duration_s: float) -> dict:
    """Wrap collected readings into the fishrand audio envelope (schema.py's
    'readings' shape), ready for fishrand.schema.validate_observations()."""
    return {
        "schema_version": AUDIO_SCHEMA_VERSION,
        "source": AUDIO_SOURCE_IDENTIFIER,
        "window_duration_s": window_duration_s,
        "readings": [{"offset_s": r.offset_s, "value": r.value} for r in readings],
    }


__all__ = [
    "AudioReading",
    "SerialReaderConfig",
    "ESP32SerialReader",
    "SerialReaderError",
    "DeviceNotFoundError",
    "DevicePermissionError",
    "DeviceDisconnectedError",
    "MalformedLineError",
    "InsufficientReadingsError",
    "to_audio_observation",
    "DEFAULT_BAUD_RATE",
    "DEFAULT_WINDOW_DURATION_S",
    "DEFAULT_MIN_READINGS",
]
