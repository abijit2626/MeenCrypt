import base64
import json

import pytest
from starlette.testclient import TestClient

from server.main import app

client = TestClient(app)


def _fish():
    return json.loads(open("examples/sample_fish_data.json", encoding="utf-8").read())


def _parse_sse(text):
    out, cur = {}, None
    for line in text.splitlines():
        if line.startswith("event:"):
            cur = line.split(":", 1)[1].strip()
            out.setdefault(cur, [])
        elif line.startswith("data:") and cur:
            out[cur].append(json.loads(line.split(":", 1)[1].strip()))
    return out


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


class TestCollectOnDemandEncrypt:
    def test_encrypt_without_fish_collects_and_binds(self):
        sse = _parse_sse(client.post("/api/encrypt", json={
            "plaintext": "guarded by a fish",
        }).text)
        done = sse["done"][0]
        assert done["status"] == "encrypted"
        pkg = done["package"]
        # fish bound into the package
        assert "fish_observations" in pkg["metadata"]
        # "push:" included: encrypt now prefers whatever live/pushed window
        # is currently displayed (see server/main.py) over re-collecting
        # from the configured sources, so encryption always matches what's
        # on screen. Earlier tests in this module already pushed a window.
        assert pkg["metadata"].get("fish_source", "").startswith(
            ("fallback:", "inbox:", "live:", "push:")
        )
        collect_step = [e for e in sse["pipe"] if e["step"] == "collect"]
        assert collect_step and collect_step[0]["status"] == "ok"

    def test_decrypt_with_bound_fish_only(self):
        sse = _parse_sse(client.post("/api/encrypt", json={
            "plaintext": "the fish guards the diary",
        }).text)
        pkg = sse["done"][0]["package"]
        sse2 = _parse_sse(client.post("/api/decrypt", json={"package": pkg}).text)
        done = sse2["done"][0]
        assert done["status"] == "decrypted"
        assert done["plaintext"] == "the fish guards the diary"
        steps = [e for e in sse2["pipe"] if e["step"] == "collect"]
        assert steps and steps[0]["detail"]["bound"] == "package"

    def test_encrypt_override_still_works(self):
        sse = _parse_sse(client.post("/api/encrypt", json={
            "plaintext": "explicit fish",
            "fish_json": _fish(),
        }).text)
        done = sse["done"][0]
        assert done["status"] == "encrypted"
        assert done["package"]["metadata"]["fish_source"] == "override:request"

    def test_decrypt_override_fish_json(self):
        sse = _parse_sse(client.post("/api/encrypt", json={
            "plaintext": "bound and overridden",
            "fish_json": self._v2_window(),
        }).text)
        pkg = sse["done"][0]["package"]
        sse2 = _parse_sse(client.post("/api/decrypt", json={
            "package": pkg,
            "fish_json": self._v2_window(),
        }).text)
        done = sse2["done"][0]
        assert done["status"] == "decrypted"
        assert done["plaintext"] == "bound and overridden"
        steps = [e for e in sse2["pipe"] if e["step"] == "collect"]
        assert steps and steps[0]["detail"]["bound"] == "override"

    def _v2_window(self):
        return {"schema_version": 2, "source": "fish_vision", "frames": [
            {"timestamp": 1750000000.0, "fish_count": 1, "activity_pct": 4.0,
             "fish": [{"id": 0, "centroid": [10.0, 20.0], "area": 100.0,
                       "speed": 1.0, "direction_rad": 0.0}]},
            {"timestamp": 1750000005.0, "fish_count": 1, "activity_pct": 5.0,
             "fish": [{"id": 0, "centroid": [12.0, 21.0], "area": 110.0,
                       "speed": 2.0, "direction_rad": 0.4}]},
        ]}

    def _v2_window_good(self):
        """Strong-activity fish window that classify_fish_quality() rates
        GOOD (>= 2 fish, >= 5% activity) - _v2_window() alone (1 fish,
        4-5% activity) is only MEDIUM under the fish/audio classifier, and
        a MEDIUM-quality window now requires audio (see fishrand/api.py
        encrypt_with_observation), so the fish-only 'code alone is enough'
        tests below need a window strong enough to skip the audio path."""
        window = self._v2_window()
        for frame in window["frames"]:
            frame["activity_pct"] = 20.0
            frame["fish_count"] = 2
            frame["fish"].append(
                {"id": 1, "centroid": [50.0, 60.0], "area": 90.0, "speed": 1.5, "direction_rad": 0.1}
            )
        return window

    def test_encrypt_with_code_makes_v3_package(self):
        sse = _parse_sse(client.post("/api/encrypt", json={
            "plaintext": "code secret",
            "code": "ab" * 32,
            "fish_json": self._v2_window_good(),
        }).text)
        done = sse["done"][0]
        assert done["status"] == "encrypted"
        pkg = done["package"]
        assert pkg["version"] == 3
        assert "os_random_b64" not in pkg
        assert "fish_commitment_b64" in pkg
        assert pkg["metadata"]["fish_source"] == "override:request"

    def test_decrypt_v2_package_needs_code(self):
        code = "cd" * 32
        sse = _parse_sse(client.post("/api/encrypt", json={
            "plaintext": "the fish guards the diary",
            "code": code,
            "fish_json": self._v2_window_good(),
        }).text)
        pkg = sse["done"][0]["package"]

        sse_ok = _parse_sse(client.post("/api/decrypt", json={
            "package": pkg, "code": code,
        }).text)
        assert sse_ok["done"][0]["status"] == "decrypted"
        assert sse_ok["done"][0]["plaintext"] == "the fish guards the diary"

        sse_bad = _parse_sse(client.post("/api/decrypt", json={
            "package": pkg, "code": "ef" * 32,
        }).text)
        assert sse_bad["done"][0]["status"] == "rejected"

        sse_none = _parse_sse(client.post("/api/decrypt", json={
            "package": pkg,
        }).text)
        assert sse_none["done"][0]["status"] == "rejected"
        assert "USB code" in sse_none["done"][0]["reason"]

    def test_tampered_package_rejected(self):
        sse = _parse_sse(client.post("/api/encrypt", json={
            "plaintext": "attack at dawn",
        }).text)
        pkg = sse["done"][0]["package"]
        blob = bytearray(base64.b64decode(pkg["payload_b64"]))
        blob[len(blob) // 2] ^= 0x01
        evil = json.loads(json.dumps(pkg))
        evil["payload_b64"] = base64.b64encode(bytes(blob)).decode()
        sse2 = _parse_sse(client.post("/api/decrypt", json={"package": evil}).text)
        assert sse2["done"][0]["status"] == "rejected"

    def test_decrypt_without_bound_fish_422(self):
        res = client.post("/api/decrypt", json={"package": {"version": 1, "metadata": {}}})
        assert res.status_code == 422


class TestDiaryPersistence:
    """The diary app's ONE persistent, server-stored encrypted document."""

    @pytest.fixture(autouse=True)
    def _isolated_diary_path(self, tmp_path, monkeypatch):
        from server import config

        monkeypatch.setattr(config, "DIARY_PATH", tmp_path / "diary.pkg")
        yield

    def test_no_diary_saved_yet(self):
        assert client.get("/api/diary").json() == {"exists": False}

    def test_unlock_without_saved_diary_404(self):
        res = client.post("/api/diary/unlock", json={"code": "ab" * 32})
        assert res.status_code == 404

    def test_save_then_state_reflects_it(self):
        code = "ab" * 32
        sse = _parse_sse(client.post("/api/diary/save", json={
            "plaintext": "dear future me", "code": code,
        }).text)
        done = sse["done"][0]
        assert done["status"] == "encrypted"
        assert done["package"]["version"] == 3
        assert "os_random_b64" not in done["package"]
        persist_step = [e for e in sse["pipe"] if e["step"] == "persist"]
        assert persist_step and persist_step[0]["status"] == "ok"

        state = client.get("/api/diary").json()
        assert state["exists"] is True
        assert state["saved_at"]
        assert state["package"]["version"] == 3
        # The stored package on disk carries no secret and no plaintext.
        assert "dear future me" not in json.dumps(state["package"])

    def test_save_then_unlock_roundtrip(self):
        code = "cd" * 32
        client.post("/api/diary/save", json={"plaintext": "the fish guards this", "code": code})
        sse = _parse_sse(client.post("/api/diary/unlock", json={"code": code}).text)
        done = sse["done"][0]
        assert done["status"] == "decrypted"
        assert done["plaintext"] == "the fish guards this"

    def test_unlock_wrong_code_rejected(self):
        client.post("/api/diary/save", json={"plaintext": "secret entry", "code": "ab" * 32})
        sse = _parse_sse(client.post("/api/diary/unlock", json={"code": "ef" * 32}).text)
        assert sse["done"][0]["status"] == "rejected"

    def test_unlock_missing_code_rejected(self):
        client.post("/api/diary/save", json={"plaintext": "secret entry", "code": "ab" * 32})
        sse = _parse_sse(client.post("/api/diary/unlock", json={}).text)
        assert sse["done"][0]["status"] == "rejected"

    def test_second_save_overwrites_the_first(self):
        code = "12" * 32
        client.post("/api/diary/save", json={"plaintext": "entry one", "code": code})
        client.post("/api/diary/save", json={"plaintext": "entry two", "code": code})

        sse = _parse_sse(client.post("/api/diary/unlock", json={"code": code}).text)
        assert sse["done"][0]["plaintext"] == "entry two"

        state = client.get("/api/diary").json()
        assert "entry one" not in json.dumps(state["package"])
        assert "entry two" not in json.dumps(state["package"])  # ciphertext only

    def test_save_requires_plaintext(self):
        res = client.post("/api/diary/save", json={"code": "ab" * 32, "plaintext": ""})
        assert res.status_code == 422