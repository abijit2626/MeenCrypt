# How FISHRAND was built

A collaboration by [abijit2626](https://github.com/abijit2626) and
[archSLAYER44](https://github.com/archSLAYER44) for the **TinkerHub Makeathon**
— theme: *Useless Projects*.

The vision side lives in our shared repo
[**MeenCrypt**](https://github.com/abijit2626/MeenCrypt) (`vision.py` watches
the fish through a webcam and streams per-frame movement data to `fish_log.json`).
The crypto module, server, CLI, and both websites live here.

The product: a goldfish guards your diary. The honest truth: the fish adds no
cryptographic entropy — the OS CSPRNG does all the real work, and that's the
whole point of the demo.

---

## Phase 1 — The joke (v1)

It started as exactly the sort of joke TinkerHub was asking for: *what if your
secrets were guarded by a goldfish?*

The first cut was small and self-contained:

```
fish sightings (samples: position, displacement, acceleration)
        ↓
SHA-256  →  fish_digest            (conditioning, not entropy)
        ↓
HKDF-SHA256(fish_digest ‖ os_random_32)
        ↓
AES-256-GCM(fresh nonce, canonical AAD)   →  ciphertext ‖ tag
```

The package was **v1**: it embedded a fresh `os_random_32` right inside the
package so anyone with the file could decrypt it. Perfect for a click-and-run
demo, terrible representation of how you'd actually keep a secret.

What mattered even that early:

- No hand-rolled PRNG, no `time` seeds, no `random.*`. The OS CSPRNG
  (`secrets.token_bytes`) was always the trusted randomness.
- The pitch was honest from day one. The README said it in plain words:
  *the fish contributes nothing cryptographically*.
- Every number was canonicalized (fixed field order, `repr` floats, no
  whitespace, NaN/±inf rejected) so the same fish ⇒ the same digest. That
  discipline is what made later versions possible.

---

## Phase 2 — Make it real (v2)

The joke had a trap: if the fish doesn't matter, why ship it? Phase 2 gave the
fish a real job and made the product something to actually demo.

### Vision contract v2

We committed to a real vision pipeline with our own `vision.py` (in
MeenCrypt). The contract moved from hand-made `samples` to camera `frames`:

```json
{"schema_version": 2, "source": "fish_vision",
 "frames": [{"timestamp": 1750000000.0, "fish_count": 1, "activity_pct": 4.2,
             "fish": [{"id": 0, "centroid": [120.5, 240.2], "area": 320.5,
                       "speed": 3.2, "direction_rad": 0.2}]}]}
```

### A collector that actually collects

A FastAPI collector watches three sources in order — NDJSON log from the
vision engine, an HTTP CV service, or a bundled sample fallback — and windows
the latest frames into one contract. Rather than inventing data, the dashboard
feeds on whatever the real fish just did, pushed over SSE.

### The universal USB code

The security model found its shape: the **USB code**. One
`secrets.token_hex(32)` written to `code.txt` on a USB stick unlocks **any**
message the fish encrypts. Each package binds its own fish window
(`metadata.fish_observations` + `fish_source`), and the key becomes

```
HKDF-SHA256(USB code ‖ fish_digest, info="FISHRAND-AES256-GCM-v2-usbcode")
```

No secret is stored in the package. Unplug the USB and the ciphertext is
opaque.

### Two websites

Split the product into two Vite apps so the demo flows through a UI, not a
terminal:

- **dashboard** (`:5173`) — fish vision telemetry + the live crypto pipeline as
  structured step events.
- **diary** (`:5174`) — write an entry → encrypt with the USB code → download a
  `.pkg`; later unlock the `.pkg` with the same stick.

### Housekeeping along the way

- Fake-fish generation was ripped out completely — the apps only ever drive
  the real collector.
- A collector bug was caught by tests: a full v2 window pushed into the NDJSON
  inbox was being wrapped as a bogus single frame instead of passing through.
- The dashboard was refactored to a single page that runs the *real* pipeline.

---

## Phase 3 — Fish-chain (v3)

### The question

We kept asking the uncomfortable version of the joke: *why can't the fish add
entropy?* If it could, the story would be better — the fish would be stuffing
real randomness into the keys.

The answer was no, and understanding *why* gave us the design:

- **Fish data is public and biased.** Anyone watching the same webcam feed can
  reconstruct the movement. It can't be a secret.
- Adding fish bytes to a key doesn't add secrecy — it only *conditions* what's
  already there.
- The only secrets in the system are genuinely secret: the OS CSPRNG output and
  the USB code created by it.

So instead of faking entropy, we gave the fish a real cryptographic job that
*doesn't* require secrecy:

### The fish-chain

In **v3 the fish drives the key schedule**. Each observation unit (a frame or a
legacy sample) eats one HMAC-SHA256 step, so the chain itself is a function of
the whole fish window:

```
state₀ = HMAC(USB code, "FISHRAND-v3" ‖ 0x01)
stateᵢ = HMAC(USB code, stateᵢ₋₁ ‖ unitᵢ ‖ i)
commitment = HMAC(USB code, "FISHRAND-v3" ‖ 0x02 ‖ state)
noise      = packed floats from the window (timestamps, activity,
             centroid, area, speed, direction)
key        = HKDF-SHA256(commitment ‖ state ‖ noise,
                        info="FISHRAND-AES256-GCM-v3-fishchain")[:32]
```

The package now carries a **public keyed commitment** (`fish_commitment_b64`).
Decrypting with different fish data — or a different USB code — makes the
commitment mismatch, and the AES-256-GCM tag refuses before a single byte of
plaintext is handed out:

```
fish_commit  mismatch → "wrong fish or USB code" → rejected
```

### Backwards compatibility

v1 and v2 packages stay fully decryptable. The universal USB code still unlocks
any message across all three versions. v2 is no longer produced — every
encrypt-with-code path now makes v3 — but the decrypt path is a permanent
citizen, which the test suite enforces.

The honest framing got sharper, not quieter:

> Fish data = physical contribution (key-schedule PRF work + binding, NOT
> secret). The USB code is the only secret. The fish signs its own work.

---

## Phase 4 — RSA goes long-term (v4)

### The problem with a shared secret

v3 was honest and it worked, but it still leaned on one shared USB code:
whoever had that code — or the `code.txt` file — could decrypt *any* message,
forever. There was no way to hand someone a single diary entry without handing
them every diary entry, past and future.

### RSA-OAEP hybrid

**v4** dropped the shared code for a long-term **RSA-3072** keypair. The
ephemeral AES-256 session key is still conditioned the same honest way (fish
digest + fresh OS CSPRNG bytes, through HKDF-SHA256) — but instead of handing
that key to whoever knows a shared code, it's wrapped with **RSA-OAEP-SHA256**:

```
fish_digest (conditioning, not entropy) ‖ os_random_32
        ↓ HKDF-SHA256
ephemeral AES-256 session key
        ↓ RSA-OAEP-SHA256(public key)          ↓ AES-256-GCM(fresh nonce)
wrapped_session_key                              ciphertext ‖ tag
```

Decryption now needs **only the matching RSA private key** — the fish window
is never required again, unlike v1/v2/v3. `fish_hash`, `fish_quality`, and
`observation_mode` still ride along in the package, but purely as **audit
metadata**: a record of what the tank looked like at encryption time, never
re-checked at decrypt time.

`cli.py` grew a `keygen` subcommand (RSA-3072, PEM out, refuses to clobber an
existing key without `--force`) and `encrypt --public-key`/
`decrypt --private-key` flags. If the private key or its USB stick isn't
present, the CLI fails safely with `PrivateKeyNotFound` — it never
auto-generates a replacement key to paper over a missing one. 25 new tests
(`tests/test_rsa_hybrid.py`) covered roundtrips, tampering, and wrong/missing
keys.

The commit that shipped this was upfront about what it didn't finish:

> Server/dashboard integration and the audio entropy source are deferred, per
> plan.

Both of those became Phase 5.

---

## Phase 5 — the mic, the drive, and the polish (v5)

### Closing v4's deferred list

**The audio entropy source arrived**: `server/audio_serial.py` reads an ESP32
+ INMP441 microphone streaming `"Sound_Level:<int>"` lines over **USB serial
at 115200 baud**. Its own error types (`DeviceNotFoundError`,
`DevicePermissionError`, `DeviceDisconnectedError`, …) deliberately don't
subclass the crypto error hierarchy — a disconnected USB cable is not a
cryptographic event. Alongside it, `fishrand/quality.py` added an adaptive
classifier that decides whether fish alone, audio alone, or both should
condition the key for a given window, so the tank isn't the only source when
the fish are sitting still.

### v1/v2/v3 retired

Closing the loop meant admitting the old promise didn't hold: Phase 3 said the
v1/v2 decrypt paths were "a permanent citizen" of the test suite. v5 removed
them anyway — `fishrand/fishchain.py` and `tests/test_fishchain.py` are gone,
and `api.py`/`mixing.py`/`rsa_hybrid.py` now say plainly that RSA-OAEP hybrid
is the *only* encryption path. The fish-chain experiment did its job — it
proved the fish could do real cryptographic work without being a secret — but
once RSA-OAEP hybrid could stand on its own, carrying three legacy decrypt
paths forever stopped being worth it.

### USB/physical-drive serial locking

The last feature of the project: diary entries can now be bound to the
**hardware serial number of a physical drive**, not just an RSA key file.
`fishrand/drive_detect.py` reads that serial off `/sys/block/.../device/serial`
for internal drives, or off the USB device descriptor's `serial` file for
flash drives, and exposes `list_drives()` / `is_present(serial)`.

`server/main.py` wires this into the diary: `POST /api/diary/entries` mints a
**fresh RSA-3072 keypair per entry** (not one shared server keypair) and bakes
the chosen drive's serial into `package["metadata"]["required_drive_serial"]`.
The shared decrypt path checks `drive_detect.is_present(...)` first and
rejects with `"drive_missing"` **before any RSA or AES work happens** if that
exact drive isn't plugged in.

In keeping with the project's honesty streak, this is documented as exactly
what it is — a server-side policy gate layered on top of v4's crypto, not new
cryptography. The landing page's own FAQ says it plainly: the CLI, given just
the key file, can still decrypt without the drive present. The lock is the
app's, not the math's.

### The rest of the polish

- **The diary became a vault.** What was a single write-then-unlock flow
  turned into a multi-entry app — calendar, entries, tags, and stats views, a
  per-entry `.pem` folder the browser remembers, mood tags, and drafts —
  instead of one document per unlock.
- **The dashboard got numbers to watch.** `server/metrics.py` streams pipeline
  latency, quality-tier mix, and op rate to `/api/metrics`; the dashboard
  picked up activity and ops-rate charts, quality-tier bars, a vault-state
  stat, a live vision preview, and a tamper test.
- **A fourth surface: the landing page.** `index.html` at the repo root is a
  standalone marketing/history/FAQ site — separate from the dashboard and
  diary Vite apps — narrating the v1 → v5 history and answering the questions
  a skeptical reader would ask (including "what if someone copies my private
  key file?", answered by the drive-lock section above).

---

## What it took

- **236 tests** across 11 test files (`tests/`), including `test_rsa_hybrid.py`
  (v4 roundtrip, tamper detection, wrong/missing keys), `test_audio_serial.py`
  and `test_drive_detect.py` (v5's mic and drive-lock hardware I/O), and
  `test_quality.py` (the adaptive fish/audio classifier). `test_fishchain.py`
  was retired along with the code path it tested.
- **Two React apps** (Vite): `dashboard/` (telemetry, live pipeline, and now
  live metrics/charts) and `diary/` (a multi-entry vault: write, encrypt,
  unlock, drive-lock).
- **A CLI** with `keygen` (RSA-3072 keypair), `encrypt --public-key`, and
  `decrypt --private-key`, resolving the private key from `--private-key`,
  `FISHRAND_RSA_PRIVATE_KEY`, or a default path — and refusing to
  auto-generate a replacement if it's missing.
- **A FastAPI server** streaming `/api/encrypt` and `/api/decrypt` as SSE, a
  live fish feed, an `/api/observations` push channel, per-entry diary routes
  (`/api/diary/entries`, `/api/diary/unlock`) with the drive-serial gate, a
  drive listing (`/api/vault/drives`), and live pipeline metrics
  (`/api/metrics`).
- **A collector** merging the NDJSON inbox → HTTP service → bundled fallback,
  now feeding both fish vision and ESP32 microphone audio.
- **Bug fixes caught by living with it:** the collector wrapping whole windows
  as bogus frames, an unrelated `fish_data` unbound variable in the decrypt
  endpoint (found by an end-to-end HTTP sanity pass), a v2 contradiction in the
  diary UI, and a key-derivation path the server tests missed.
- **Discipline:** every change ran `pytest`, both apps linted and built clean,
  HTTP round-trips ran for every package version before anything was
  considered done.

---

## Repo structure

```
README.md                  the GitHub pitch
index.html                 v5: standalone landing page — history + FAQ
documentary/
  how-it-was-built.md      this file
cli.py                     keygen / encrypt --public-key / decrypt --private-key
fishrand/                  core cryptography (pure Python)
  schema.py                v1 samples + v2 vision frames + audio contract
  canonicalize.py          deterministic serialization
  observe.py               metadata extraction for the dashboard
  entropy.py               SHA-256 conditioning → fish_digest
  cspng.py                 OS CSPRNG + generate_usb_code()
  mixing.py                HKDF + domain-separation info strings
  quality.py                v5: adaptive fish/audio quality classifier
  crypto.py                 AES-256-GCM + canonical AAD
  errors.py                 shared FishrandError / AuthenticationFailure base
  rsa_hybrid.py              v4: RSA-3072 keygen + RSA-OAEP wrap/unwrap
  drive_detect.py            v5: hardware-serial USB/drive locking
  api.py                     encrypt_with_rsa_hybrid / decrypt_rsa_hybrid_package
  package.py                 v4 build + parse (the only package version now)
  dashboard.py               structured pipeline events
server/
  main.py                    /api/encrypt, /api/decrypt (SSE), diary vault
                              routes + drive-serial gate, fish feed
  collector.py                NDJSON inbox → URL → sample fallback
  audio_serial.py             v5: ESP32 + INMP441 mic over USB serial
  metrics.py                  v5: pipeline latency/quality/rate → /api/metrics
  config.py                   env config
dashboard/                    React (Vite) — telemetry + live pipeline + metrics
diary/                        React (Vite) — multi-entry vault, drive-lock unlock
tests/                        236 tests
examples/                     sample windows, fish_sender.py, a diary
vision.py                     → lives in MeenCrypt (our shared repo)
```