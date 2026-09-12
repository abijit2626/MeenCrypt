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
