import base64
import json

import pytest
from starlette.testclient import TestClient

from server.main import app

client = TestClient(app)


def _fish():
    return json.loads(open("examples/sample_fish_data.json", encoding="utf-8").read())


def _good_fish():
    """GOOD-quality v2 window (>=2 fish, >=5% activity) so encrypt/save
    never needs an ESP32 audio window to succeed - see fishrand/quality.py."""
    return {
        "schema_version": 2,
        "source": "fish_vision",
        "frames": [
            {"timestamp": 1750000000.0, "fish_count": 2, "activity_pct": 20.0, "fish": [
                {"id": 0, "centroid": [10.0, 20.0], "area": 100.0, "speed": 1.0, "direction_rad": 0.0},
                {"id": 1, "centroid": [30.0, 40.0], "area": 90.0, "speed": 1.5, "direction_rad": 0.1},
            ]},
        ],
    }


def _parse_sse(text):
    out, cur = {}, None
    for line in text.splitlines():
        if line.startswith("event:"):
            cur = line.split(":", 1)[1].strip()
            out.setdefault(cur, [])
        elif line.startswith("data:") and cur:
            out[cur].append(json.loads(line.split(":", 1)[1].strip()))
    return out


@pytest.fixture()
def keys(tmp_path, monkeypatch):
    """Point the server at an isolated, per-test keys directory and
    generate a fresh RSA keypair via the real /api/keys/generate endpoint.
    Returns the private key PEM text (never stored server-side)."""
    from server import config

    monkeypatch.setattr(config, "PUBLIC_KEY_PATH", tmp_path / "keys" / "public_key.pem")
    res = client.post("/api/keys/generate", json={})
    assert res.status_code == 200
    return res.text


class TestHealth:
    def test_health(self):
        body = client.get("/api/health").json()
        assert body["status"] == "ok"
        assert body["module"] == "fishrand"
        assert body["fish_source"].startswith(("fallback:", "live:", "inbox:"))


class TestObservations:
    def test_push_raw_minimal_accepted(self):
        raw = {"samples": [
            {"position": {"x": 10, "y": 10}, "displacement": {"x": 1, "y": 0},
             "acceleration": {"x": 0, "y": 0}},
        ]}
        res = client.post("/api/observations", json={"observations": raw})
        assert res.status_code == 200
        assert res.json()["accepted"] is True

    def test_push_vision_frame_accepted(self):
        frame = {"timestamp": 1750000000.0, "fish_count": 1, "activity_pct": 4.0,
                 "fish": [{"id": 0, "centroid": [10.0, 20.0], "area": 100.0,
                           "speed": 1.0, "direction_rad": 0.0}]}
        res = client.post("/api/observations", json={"observations": frame})
        assert res.status_code == 200
        assert res.json()["accepted"] is True

    def test_push_bad_data_rejected_422(self):
        res = client.post("/api/observations", json={
            "observations": {"samples": [{"position": {"x": "not-a-number"}}]},
        })
        assert res.status_code == 422

    def test_current_returns_last_push(self):
        fish = _fish()
        client.post("/api/observations", json={"observations": fish})
        body = client.get("/api/observations/current").json()
        assert body["source"].startswith("push:")
        assert len(body["observations"]["samples"]) == len(fish["samples"])


class TestKeyGeneration:
    def test_generate_returns_private_key_pem_and_stores_only_public(self, tmp_path, monkeypatch):
        from server import config

        pub_path = tmp_path / "keys" / "public_key.pem"
        monkeypatch.setattr(config, "PUBLIC_KEY_PATH", pub_path)

        res = client.post("/api/keys/generate", json={})
        assert res.status_code == 200
        assert res.headers["content-disposition"] == 'attachment; filename="private_key.pem"'
        assert "BEGIN PRIVATE KEY" in res.text or "BEGIN ENCRYPTED PRIVATE KEY" in res.text

        assert pub_path.exists()
        assert "BEGIN PUBLIC KEY" in pub_path.read_text(encoding="utf-8")
        # The private key must never be written server-side - the public
        # key is the only file this endpoint leaves behind.
        assert list(pub_path.parent.iterdir()) == [pub_path]

    def test_generate_refuses_to_clobber_without_force(self, tmp_path, monkeypatch):
        from server import config

        monkeypatch.setattr(config, "PUBLIC_KEY_PATH", tmp_path / "keys" / "public_key.pem")
        assert client.post("/api/keys/generate", json={}).status_code == 200
        res = client.post("/api/keys/generate", json={})
        assert res.status_code == 409

    def test_generate_force_overwrites(self, tmp_path, monkeypatch):
        from server import config

        monkeypatch.setattr(config, "PUBLIC_KEY_PATH", tmp_path / "keys" / "public_key.pem")
        first = client.post("/api/keys/generate", json={}).text
        second = client.post("/api/keys/generate", json={"force": True})
        assert second.status_code == 200
        assert second.text != first


class TestCollectOnDemandEncrypt:
    def test_encrypt_without_key_rejected_409(self, tmp_path, monkeypatch):
        from server import config

        monkeypatch.setattr(config, "PUBLIC_KEY_PATH", tmp_path / "keys" / "public_key.pem")
        res = client.post("/api/encrypt", json={"plaintext": "no key yet", "fish_json": _good_fish()})
        assert res.status_code == 409

    def test_encrypt_makes_v4_package_no_secret_needed(self, keys):
        sse = _parse_sse(client.post("/api/encrypt", json={
            "plaintext": "guarded by a fish",
            "fish_json": _good_fish(),
        }).text)
        done = sse["done"][0]
        assert done["status"] == "encrypted"
        pkg = done["package"]
        assert pkg["version"] == 4
        assert "encrypted_session_key_b64" in pkg
        assert pkg["metadata"]["fish_source"] == "override:request"
        collect_step = [e for e in sse["pipe"] if e["step"] == "collect"]
        assert collect_step and collect_step[0]["status"] == "ok"

    def test_decrypt_needs_only_the_private_key(self, keys):
        sse = _parse_sse(client.post("/api/encrypt", json={
            "plaintext": "the fish guards the diary",
            "fish_json": _good_fish(),
        }).text)
        pkg = sse["done"][0]["package"]
        sse2 = _parse_sse(client.post("/api/decrypt", json={
            "package": pkg, "private_key_pem": keys,
        }).text)
        done = sse2["done"][0]
        assert done["status"] == "decrypted"
        assert done["plaintext"] == "the fish guards the diary"

    def test_decrypt_missing_private_key_rejected_422(self, keys):
        sse = _parse_sse(client.post("/api/encrypt", json={
            "plaintext": "guarded", "fish_json": _good_fish(),
        }).text)
        pkg = sse["done"][0]["package"]
        res = client.post("/api/decrypt", json={"package": pkg})
        assert res.status_code == 422

    def test_decrypt_wrong_private_key_rejected(self, keys):
        from fishrand.rsa_hybrid import generate_keypair, serialize_private_key

        sse = _parse_sse(client.post("/api/encrypt", json={
            "plaintext": "guarded", "fish_json": _good_fish(),
        }).text)
        pkg = sse["done"][0]["package"]
        wrong_private, _ = generate_keypair()
        wrong_pem = serialize_private_key(wrong_private, None).decode("utf-8")
        sse2 = _parse_sse(client.post("/api/decrypt", json={
            "package": pkg, "private_key_pem": wrong_pem,
        }).text)
        assert sse2["done"][0]["status"] == "rejected"

    def test_tampered_package_rejected(self, keys):
        sse = _parse_sse(client.post("/api/encrypt", json={
            "plaintext": "attack at dawn", "fish_json": _good_fish(),
        }).text)
        pkg = sse["done"][0]["package"]
        blob = bytearray(base64.b64decode(pkg["payload_b64"]))
        blob[len(blob) // 2] ^= 0x01
        evil = json.loads(json.dumps(pkg))
        evil["payload_b64"] = base64.b64encode(bytes(blob)).decode()
        sse2 = _parse_sse(client.post("/api/decrypt", json={
            "package": evil, "private_key_pem": keys,
        }).text)
        assert sse2["done"][0]["status"] == "rejected"

    def test_decrypt_missing_package_422(self, keys):
        res = client.post("/api/decrypt", json={"private_key_pem": keys})
        assert res.status_code == 422


class TestDiaryPersistence:
    """The diary app's ONE persistent, server-stored encrypted document."""

    @pytest.fixture(autouse=True)
    def _isolated_diary_path(self, tmp_path, monkeypatch):
        from server import config

        monkeypatch.setattr(config, "DIARY_PATH", tmp_path / "diary.pkg")
        yield

    def test_no_diary_saved_yet(self, tmp_path, monkeypatch):
        from server import config

        monkeypatch.setattr(config, "PUBLIC_KEY_PATH", tmp_path / "keys" / "public_key.pem")
        body = client.get("/api/diary").json()
        assert body == {"exists": False, "encryption_key_exists": False}

    def test_state_reports_key_existence(self, keys):
        body = client.get("/api/diary").json()
        assert body["encryption_key_exists"] is True

    def test_save_without_key_rejected_409(self, tmp_path, monkeypatch):
        from server import config

        monkeypatch.setattr(config, "PUBLIC_KEY_PATH", tmp_path / "keys" / "public_key.pem")
        res = client.post("/api/diary/save", json={"plaintext": "dear future me"})
        assert res.status_code == 409

    def test_unlock_without_saved_diary_404(self, keys):
        res = client.post("/api/diary/unlock", json={"private_key_pem": keys})
        assert res.status_code == 404

    def test_save_then_state_reflects_it(self, keys, monkeypatch):
        # A GOOD-quality live/pushed window means /api/diary/save (which
        # never sends fish_json - see _resolve_fish) doesn't need audio.
        from server import main as server_main

        monkeypatch.setattr(server_main, "_CURRENT", _good_fish())
        monkeypatch.setattr(server_main, "_CURRENT_SOURCE", "test:pushed")

        sse = _parse_sse(client.post("/api/diary/save", json={"plaintext": "dear future me"}).text)
        done = sse["done"][0]
        assert done["status"] == "encrypted"
        assert done["package"]["version"] == 4
        persist_step = [e for e in sse["pipe"] if e["step"] == "persist"]
        assert persist_step and persist_step[0]["status"] == "ok"

        state = client.get("/api/diary").json()
        assert state["exists"] is True
        assert state["saved_at"]
        assert state["encryption_key_exists"] is True
        assert state["package"]["version"] == 4
        # The stored package on disk carries no secret and no plaintext.
        assert "dear future me" not in json.dumps(state["package"])

    def test_save_then_unlock_roundtrip(self, keys, monkeypatch):
        from server import main as server_main

        monkeypatch.setattr(server_main, "_CURRENT", _good_fish())
        monkeypatch.setattr(server_main, "_CURRENT_SOURCE", "test:pushed")

        client.post("/api/diary/save", json={"plaintext": "the fish guards this"})
        sse = _parse_sse(client.post("/api/diary/unlock", json={"private_key_pem": keys}).text)
        done = sse["done"][0]
        assert done["status"] == "decrypted"
        assert done["plaintext"] == "the fish guards this"

    def test_unlock_wrong_private_key_rejected(self, keys, monkeypatch):
        from fishrand.rsa_hybrid import generate_keypair, serialize_private_key
        from server import main as server_main

        monkeypatch.setattr(server_main, "_CURRENT", _good_fish())
        monkeypatch.setattr(server_main, "_CURRENT_SOURCE", "test:pushed")

        client.post("/api/diary/save", json={"plaintext": "secret entry"})
        wrong_private, _ = generate_keypair()
        wrong_pem = serialize_private_key(wrong_private, None).decode("utf-8")
        sse = _parse_sse(client.post("/api/diary/unlock", json={"private_key_pem": wrong_pem}).text)
        assert sse["done"][0]["status"] == "rejected"

    def test_unlock_missing_private_key_rejected_422(self, keys, monkeypatch):
        from server import main as server_main

        monkeypatch.setattr(server_main, "_CURRENT", _good_fish())
        monkeypatch.setattr(server_main, "_CURRENT_SOURCE", "test:pushed")

        client.post("/api/diary/save", json={"plaintext": "secret entry"})
        res = client.post("/api/diary/unlock", json={})
        assert res.status_code == 422

    def test_second_save_overwrites_the_first(self, keys, monkeypatch):
        from server import main as server_main

        monkeypatch.setattr(server_main, "_CURRENT", _good_fish())
        monkeypatch.setattr(server_main, "_CURRENT_SOURCE", "test:pushed")

        client.post("/api/diary/save", json={"plaintext": "entry one"})
        client.post("/api/diary/save", json={"plaintext": "entry two"})

        sse = _parse_sse(client.post("/api/diary/unlock", json={"private_key_pem": keys}).text)
        assert sse["done"][0]["plaintext"] == "entry two"

        state = client.get("/api/diary").json()
        assert "entry one" not in json.dumps(state["package"])
        assert "entry two" not in json.dumps(state["package"])  # ciphertext only

    def test_save_requires_plaintext(self, keys):
        res = client.post("/api/diary/save", json={"plaintext": ""})
        assert res.status_code == 422


_FAKE_DRIVE = {"serial": "TEST-SERIAL-0001", "label": "Test USB Stick", "mountpoint": "/mnt/test-usb"}


class TestDiaryEntries:
    """Multi-entry diary: one encrypted package per entry + plaintext flags."""

    @pytest.fixture(autouse=True)
    def _isolated_entries(self, tmp_path, monkeypatch):
        from server import config
        from server import main as server_main

        monkeypatch.setattr(config, "DIARY_PATH", tmp_path / "diary.pkg")
        monkeypatch.setattr(config, "ENTRIES_DIR", tmp_path / "entries")
        monkeypatch.setattr(server_main, "_CURRENT", _good_fish())
        monkeypatch.setattr(server_main, "_CURRENT_SOURCE", "test:pushed")
        monkeypatch.setattr(server_main.drive_detect, "list_drives", lambda: [dict(_FAKE_DRIVE)])
        monkeypatch.setattr(server_main.drive_detect, "is_present", lambda serial: serial == _FAKE_DRIVE["serial"])
        yield

    def _create(self, plaintext, drive_serial=_FAKE_DRIVE["serial"]):
        sse = _parse_sse(client.post(
            "/api/diary/entries", json={"plaintext": plaintext, "drive_serial": drive_serial},
        ).text)
        return sse["done"][0]

    def test_empty_list(self, keys):
        body = client.get("/api/diary/entries").json()
        assert body == {"encryption_key_exists": True, "entries": []}

    def test_create_list_decrypt_roundtrip(self, keys):
        first = self._create("entry one")
        second = self._create("entry two")
        assert first["status"] == "encrypted" and first["id"] != second["id"]
        assert first["flags"] == {"pinned": False, "archived": False, "deleted_at": None}
        # Each entry gets its OWN keypair - never the shared one.
        assert "BEGIN PRIVATE KEY" in first["private_key_pem"]
        assert first["private_key_pem"] != second["private_key_pem"]

        entries = client.get("/api/diary/entries").json()["entries"]
        assert {e["id"] for e in entries} == {first["id"], second["id"]}
        assert "entry one" not in json.dumps(entries)

        pkg = next(e["package"] for e in entries if e["id"] == first["id"])
        assert pkg["metadata"]["required_drive_serial"] == _FAKE_DRIVE["serial"]
        assert pkg["metadata"]["required_drive_label"] == _FAKE_DRIVE["label"]
        sse = _parse_sse(client.post("/api/decrypt", json={"package": pkg, "private_key_pem": first["private_key_pem"]}).text)
        assert sse["done"][0]["plaintext"] == "entry one"

        # The shared keypair from `keys` never touches per-entry packages.
        wrong = _parse_sse(client.post("/api/decrypt", json={"package": pkg, "private_key_pem": keys}).text)
        assert wrong["done"][0]["status"] == "rejected"
        assert wrong["done"][0]["code"] == "auth_failed"

    def test_create_rejects_unmounted_drive(self, keys):
        res = client.post("/api/diary/entries", json={"plaintext": "x", "drive_serial": "not-a-real-drive"})
        assert res.status_code == 422

    def test_decrypt_blocked_without_required_drive(self, keys, monkeypatch):
        from server import main as server_main

        entry = self._create("needs my usb")
        pkg = client.get("/api/diary/entries").json()["entries"][0]["package"]

        # Drive unplugged: correct key still isn't enough.
        monkeypatch.setattr(server_main.drive_detect, "is_present", lambda serial: False)
        blocked = _parse_sse(client.post(
            "/api/decrypt", json={"package": pkg, "private_key_pem": entry["private_key_pem"]},
        ).text)
        assert blocked["done"][0]["status"] == "rejected"
        assert blocked["done"][0]["code"] == "drive_missing"
        assert _FAKE_DRIVE["label"] in blocked["done"][0]["reason"]

        # Drive plugged back in: same key now works.
        monkeypatch.setattr(server_main.drive_detect, "is_present", lambda serial: serial == _FAKE_DRIVE["serial"])
        ok = _parse_sse(client.post(
            "/api/decrypt", json={"package": pkg, "private_key_pem": entry["private_key_pem"]},
        ).text)
        assert ok["done"][0]["status"] == "decrypted"

    def test_old_style_package_without_drive_requirement_unaffected(self, keys, monkeypatch):
        """A package with no required_drive_serial (e.g. from /api/encrypt)
        never triggers the drive check at all."""
        from server import main as server_main

        monkeypatch.setattr(server_main.drive_detect, "is_present", lambda serial: False)
        enc = _parse_sse(client.post("/api/encrypt", json={"plaintext": "no drive needed"}).text)
        pkg = enc["done"][0]["package"]
        assert "required_drive_serial" not in pkg["metadata"]
        dec = _parse_sse(client.post("/api/decrypt", json={"package": pkg, "private_key_pem": keys}).text)
        assert dec["done"][0]["status"] == "decrypted"

    def test_vault_drives_endpoint(self, keys):
        assert client.get("/api/vault/drives").json() == {"drives": [_FAKE_DRIVE]}

    def test_flags_persist(self, keys):
        entry_id = self._create("pin me")["id"]
        res = client.patch(f"/api/diary/entries/{entry_id}", json={"pinned": True, "archived": True})
        assert res.status_code == 200
        flags = client.get("/api/diary/entries").json()["entries"][0]["flags"]
        assert flags["pinned"] is True and flags["archived"] is True and flags["deleted_at"] is None

    def test_delete_only_from_bin(self, keys):
        entry_id = self._create("bin me")["id"]
        assert client.delete(f"/api/diary/entries/{entry_id}").status_code == 409

        client.patch(f"/api/diary/entries/{entry_id}", json={"deleted": True})
        assert client.get("/api/diary/entries").json()["entries"][0]["flags"]["deleted_at"]
        assert client.delete(f"/api/diary/entries/{entry_id}").status_code == 200
        assert client.get("/api/diary/entries").json()["entries"] == []

    def test_invalid_and_unknown_ids(self, keys):
        assert client.patch("/api/diary/entries/..%2Fdiary", json={"pinned": True}).status_code in (404, 422)
        assert client.patch("/api/diary/entries/not-an-id", json={"pinned": True}).status_code == 422
        assert client.delete(f"/api/diary/entries/{'0' * 32}").status_code == 404

    def test_legacy_diary_copied_not_moved(self, keys):
        from server import config

        client.post("/api/diary/save", json={"plaintext": "the old single diary"})
        entries = client.get("/api/diary/entries").json()["entries"]
        assert [e["id"] for e in entries] == ["legacy"]
        assert config.DIARY_PATH.exists()

        client.patch("/api/diary/entries/legacy", json={"deleted": True})
        client.delete("/api/diary/entries/legacy")
        assert client.get("/api/diary/entries").json()["entries"] == []


class TestMetrics:
    """Dashboard telemetry (server/metrics.py) fed by real encrypt/decrypt
    calls - see /api/metrics."""

    @pytest.fixture(autouse=True)
    def _isolated_metrics(self, monkeypatch):
        from server import main as server_main
        from server import metrics

        monkeypatch.setattr(server_main, "_CURRENT", _good_fish())
        monkeypatch.setattr(server_main, "_CURRENT_SOURCE", "test:pushed")
        metrics._reset_for_tests()
        yield
        metrics._reset_for_tests()

    def test_empty_snapshot(self):
        body = client.get("/api/metrics").json()
        assert body["fish_activity"] == []
        assert body["quality_tiers"] == {"GOOD": 0, "MEDIUM": 0, "BAD": 0}
        assert body["ops"]["encrypt"]["rate_per_min"] == 0
        assert body["latency_ms"]["kdf"] == {"p50": None, "p95": None, "n": 0}
        assert body["latency_ms"]["wrap"] == {"p50": None, "p95": None, "n": 0}

    def test_encrypt_feeds_latency_and_rate(self, keys):
        sse = _parse_sse(client.post("/api/encrypt", json={"plaintext": "hi"}).text)
        assert sse["done"][0]["status"] == "encrypted"

        body = client.get("/api/metrics").json()
        assert body["latency_ms"]["kdf"]["n"] == 1
        assert body["latency_ms"]["kdf"]["p50"] is not None and body["latency_ms"]["kdf"]["p50"] >= 0
        assert body["latency_ms"]["wrap"]["n"] == 1
        assert body["latency_ms"]["wrap"]["p50"] is not None and body["latency_ms"]["wrap"]["p50"] >= 0
        assert body["ops"]["encrypt"]["rate_per_min"] == 1
        assert body["ops"]["decrypt"]["rate_per_min"] == 0

    def test_decrypt_feeds_rate_but_not_encrypt_latency(self, keys):
        pkg = _parse_sse(client.post("/api/encrypt", json={"plaintext": "hi"}).text)["done"][0]["package"]
        before = client.get("/api/metrics").json()["latency_ms"]["kdf"]["n"]

        sse = _parse_sse(client.post("/api/decrypt", json={"package": pkg, "private_key_pem": keys}).text)
        assert sse["done"][0]["plaintext"] == "hi"

        body = client.get("/api/metrics").json()
        assert body["latency_ms"]["kdf"]["n"] == before  # decrypt doesn't run HKDF
        assert body["ops"]["decrypt"]["rate_per_min"] == 1

    def test_fish_poll_tick_records_activity_and_quality(self):
        from server import main as server_main

        # Run exactly one tick of _fish_poll_loop's body by calling the same
        # helpers it calls, rather than the infinite loop itself.
        from fishrand.observe import observation_stats
        from fishrand.quality import classify_fish_quality
        from server import config, metrics

        data = server_main._CURRENT
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

        body = client.get("/api/metrics").json()
        assert len(body["fish_activity"]) == 1
        assert body["fish_activity"][0]["fish_count"] == 2
        assert body["quality_tiers"]["GOOD"] == 1  # _good_fish() is GOOD-quality by construction


class TestVaultStatus:
    """Cosmetic LOCKED/UNLOCKED relay the diary app pushes so other apps
    (the dashboard) can show it too - never the key, never content."""

    @pytest.fixture(autouse=True)
    def _isolated_vault(self, monkeypatch):
        from server import main as server_main

        monkeypatch.setattr(server_main, "_VAULT_UNLOCKED", False)
        monkeypatch.setattr(server_main, "_VAULT_AT", None)
        yield

    def test_default_locked(self):
        assert client.get("/api/vault/status").json() == {"unlocked": False, "updated_at": None}

    def test_post_flips_and_persists(self):
        res = client.post("/api/vault/status", json={"unlocked": True})
        assert res.status_code == 200
        assert res.json()["unlocked"] is True
        assert res.json()["updated_at"]

        again = client.get("/api/vault/status").json()
        assert again["unlocked"] is True
        assert again["updated_at"] == res.json()["updated_at"]

        back = client.post("/api/vault/status", json={"unlocked": False})
        assert back.json()["unlocked"] is False


class TestVisionSnapshot:
    """/api/vision/snapshot just reads whatever vision.py last wrote to
    config.VISION_FRAME_PATH - never touches a camera itself."""

    @pytest.fixture(autouse=True)
    def _isolated_frame_path(self, tmp_path, monkeypatch):
        from server import config

        monkeypatch.setattr(config, "VISION_FRAME_PATH", tmp_path / "vision" / "frame.jpg")
        yield

    def test_404_before_any_frame(self):
        assert client.get("/api/vision/snapshot").status_code == 404

    def test_200_once_a_frame_exists(self):
        from server import config

        config.VISION_FRAME_PATH.parent.mkdir(parents=True, exist_ok=True)
        config.VISION_FRAME_PATH.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg-bytes")

        res = client.get("/api/vision/snapshot")
        assert res.status_code == 200
        assert res.headers["content-type"] == "image/jpeg"
        assert res.content == b"\xff\xd8\xff\xe0fake-jpeg-bytes"

