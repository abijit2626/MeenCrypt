"""Tests for the ESP32 serial audio reader (server/audio_serial.py).

All tests use a dependency-injected fake serial object - no real ESP32 or
pyserial hardware is ever touched.
"""

from __future__ import annotations

import pytest

from server.audio_serial import (
    AudioReading,
    DeviceDisconnectedError,
    DeviceNotFoundError,
    DevicePermissionError,
    ESP32SerialReader,
    InsufficientReadingsError,
    MalformedLineError,
    SOUND_LEVEL_MAX,
    SerialReaderConfig,
    to_audio_observation,
)


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def advance(self, dt):
        self.t += dt

    def monotonic(self):
        return self.t


class FakeSerial:
    """Each readline() call pops one queued line and advances the fake
    clock by `tick`, simulating a steady stream from the ESP32."""

    def __init__(self, lines, clock, tick=0.01):
        self.lines = list(lines)
        self.clock = clock
        self.tick = tick
        self.closed = False
        self.in_waiting = 1

    def readline(self):
        self.clock.advance(self.tick)
        if self.lines:
            line = self.lines.pop(0)
            self.in_waiting = 1 if self.lines else 0
            return line
        self.in_waiting = 0
        return b""

    def close(self):
        self.closed = True


def _reader(lines, clock, **config_kwargs):
    cfg = SerialReaderConfig(port="fake", **config_kwargs)
    fake = FakeSerial(lines, clock)
    return ESP32SerialReader(cfg, serial_factory=lambda p, b, t: fake), fake


# --- parse_line --------------------------------------------------------


def test_parse_line_valid():
    assert ESP32SerialReader.parse_line(b"Sound_Level:12345\n") == 12345


def test_parse_line_ignores_empty():
    assert ESP32SerialReader.parse_line(b"") is None
    assert ESP32SerialReader.parse_line(b"\n") is None


def test_parse_line_ignores_unrelated_text():
    assert ESP32SerialReader.parse_line(b"Booting ESP32...\n") is None


def test_parse_line_rejects_non_numeric():
    with pytest.raises(MalformedLineError):
        ESP32SerialReader.parse_line(b"Sound_Level:abc\n")


def test_parse_line_rejects_negative():
    with pytest.raises(MalformedLineError):
        ESP32SerialReader.parse_line(b"Sound_Level:-5\n")


def test_parse_line_rejects_out_of_bounds():
    with pytest.raises(MalformedLineError):
        ESP32SerialReader.parse_line(f"Sound_Level:{SOUND_LEVEL_MAX + 1}\n".encode())


def test_parse_line_handles_undecodable_bytes_gracefully():
    # errors="replace" never raises on bad bytes; the replaced text simply
    # won't match the Sound_Level prefix, so it's ignored like any other
    # unrelated line.
    assert ESP32SerialReader.parse_line(b"\xff\xfe garbage") is None


# --- read_window ---------------------------------------------------------


def test_read_window_collects_multiple_readings(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr("server.audio_serial.time.monotonic", clock.monotonic)
    lines = [b"Sound_Level:12345\n", b"garbage\n", b"Sound_Level:12890\n", b"Sound_Level:11920\n"]
    reader, _fake = _reader(lines, clock, window_duration_s=1.0, min_readings=2)
    with reader:
        readings = reader.read_window()
    assert [r.value for r in readings] == [12345, 12890, 11920]
    assert all(isinstance(r, AudioReading) for r in readings)


def test_read_window_skips_malformed_lines_without_aborting(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr("server.audio_serial.time.monotonic", clock.monotonic)
    lines = [b"Sound_Level:12345\n", b"Sound_Level:notanumber\n", b"Sound_Level:12890\n"]
    reader, _fake = _reader(lines, clock, window_duration_s=1.0, min_readings=2)
    with reader:
        readings = reader.read_window()
    assert [r.value for r in readings] == [12345, 12890]


def test_read_window_insufficient_readings_raises(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr("server.audio_serial.time.monotonic", clock.monotonic)
    reader, _fake = _reader([b"Sound_Level:1\n"], clock, window_duration_s=0.05, min_readings=5)
    with reader:
        with pytest.raises(InsufficientReadingsError):
            reader.read_window()


def test_read_window_disconnect_raises(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr("server.audio_serial.time.monotonic", clock.monotonic)

    class DisconnectingSerial:
        def readline(self):
            raise OSError("device disconnected")

        def close(self):
            pass

    cfg = SerialReaderConfig(port="fake", window_duration_s=1.0)
    reader = ESP32SerialReader(cfg, serial_factory=lambda p, b, t: DisconnectingSerial())
    with reader:
        with pytest.raises(DeviceDisconnectedError):
            reader.read_window()


def test_read_window_before_open_raises():
    cfg = SerialReaderConfig(port="fake")
    reader = ESP32SerialReader(cfg, serial_factory=lambda p, b, t: FakeSerial([], FakeClock()))
    with pytest.raises(Exception):
        reader.read_window()


def test_read_window_records_increasing_offsets(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr("server.audio_serial.time.monotonic", clock.monotonic)
    lines = [b"Sound_Level:1\n", b"Sound_Level:2\n"]
    reader, fake = _reader(lines, clock, window_duration_s=1.0, min_readings=1)
    fake.tick = 0.1
    with reader:
        readings = reader.read_window()
    assert readings[0].offset_s < readings[1].offset_s


# --- open() error classification -----------------------------------------


def test_open_raises_device_not_found():
    cfg = SerialReaderConfig(port="/dev/ttyUSB_does_not_exist_fishrand_test")
    reader = ESP32SerialReader(cfg)
    with pytest.raises(DeviceNotFoundError):
        reader.open()


def test_open_translates_permission_style_errors():
    def busy_factory(port, baud, timeout):
        raise RuntimeError("could not exclusively lock port")

    cfg = SerialReaderConfig(port="fake")
    reader = ESP32SerialReader(cfg, serial_factory=busy_factory)
    with pytest.raises(DevicePermissionError):
        reader.open()


def test_close_is_idempotent():
    clock = FakeClock()
    reader, fake = _reader([], clock)
    reader.open()
    reader.close()
    reader.close()  # must not raise
    assert fake.closed


# --- to_audio_observation --------------------------------------------------


def test_to_audio_observation_shape():
    from fishrand.schema import validate_observations

    readings = [AudioReading(value=1, offset_s=0.0), AudioReading(value=2, offset_s=0.5)]
    obs = to_audio_observation(readings, window_duration_s=5.0)
    validated = validate_observations(obs)
    assert validated["readings"] == [{"offset_s": 0.0, "value": 1}, {"offset_s": 0.5, "value": 2}]
    assert validated["window_duration_s"] == 5.0
