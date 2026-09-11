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

## What it took

- **137 tests** across 8 test files (`tests/`), including a dedicated
  `test_fishchain.py` (determinism, wrong-frame/wrong-code ⇒ commitment
  mismatch, noise bytes, v3 disk round-trip, no secret).
- **Two React apps** (Vite): `dashboard/` (telemetry + live pipeline) and
  `diary/` (encrypt/download/unlock).
- **A CLI** with `init` (makes `code.txt`), `encrypt`, and `decrypt`, resolving
  the code from `--code`, `--usb`, `FISHRAND_USB_CODE`, or `./code.txt`.
- **A FastAPI server** streaming `/api/encrypt` and `/api/decrypt` as SSE, a
  live fish feed, and an `/api/observations` push channel.
- **A collector** merging the NDJSON inbox → HTTP service → bundled fallback.
- **Bug fixes caught by living with it:** the collector wrapping whole windows
  as bogus frames, an unrelated `fish_data` unbound variable in the decrypt
  endpoint (found by an end-to-end HTTP sanity pass), a v2 contradiction in the
  diary UI, and a key-derivation path the server tests missed.
- **Discipline:** every change ran `pytest`, both apps linted and built clean,
  HTTP round-trips ran for v1, v2, and v3 before anything was considered done.

---

## Repo structure

```
README.md                  the GitHub pitch + version table
documentary/
  how-it-was-built.md      this file
cli.py                     init / encrypt / decrypt with the USB code
fishrand/                  core cryptography (pure Python)
  schema.py                v1 samples + v2 vision frames contract
  canonicalize.py          deterministic serialization
  observe.py               metadata extraction for the dashboard
  entropy.py               SHA-256 conditioning → fish_digest
  cspng.py                 OS CSPRNG + generate_usb_code()
  mixing.py                HKDF + domain-separation info strings
  fishchain.py             v3: per-unit HMAC chain, noise, commitment
  crypto.py                AES-256-GCM + canonical AAD
  api.py                   encrypt_with_fish_entropy / decrypt_package
  package.py               v1/v2/v3 build + parse
  dashboard.py             structured pipeline events
server/
  main.py                  /api/encrypt + /api/decrypt (SSE), fish feed
  collector.py             NDJSON inbox → URL → sample fallback
  config.py                env config
dashboard/                 React (Vite) — fish telemetry + live pipeline
diary/                     React (Vite) — write → encrypt → download .pkg
tests/                     137 tests
examples/                  sample windows, fish_sender.py, a diary
vision.py                  → lives in MeenCrypt (our shared repo)
```