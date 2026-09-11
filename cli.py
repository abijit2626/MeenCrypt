"""FISHRAND CLI - encrypt/decrypt a diary from the terminal.

The RSA keypair story:

  1. Make the keypair once; move the private key to your USB stick
        python cli.py keygen --output /media/USB/fishrand
  2. Encrypt with the PUBLIC key (safe to leave on this machine). The fish
     window is collected automatically, and the ESP32 mic fills in when
     the fish are too still to be useful
        python cli.py encrypt --diary examples/diary.txt \
            --public-key /media/USB/fishrand/public_key.pem \
            --out diary.pkg
  3. Unlock later - anywhere - with the PRIVATE key and nothing else
        python cli.py decrypt --pkg diary.pkg \
            --private-key /media/USB/fishrand/private_key.pem

The private key is the only secret. The package carries no secret at all,
and the fish/audio observations are audit metadata - they are never needed
again to decrypt.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

from fishrand import (
    AuthenticationFailure,
    decrypt_rsa_hybrid_package,
    encrypt_with_observation,
    load_observations,
    load_package,
    save_package,
)

PRIVATE_KEY_FILENAME = "private_key.pem"
PUBLIC_KEY_FILENAME = "public_key.pem"


def _resolve_private_key_path(args: argparse.Namespace) -> pathlib.Path | None:
    """Locate the RSA private key: --private-key <file>, else
    FISHRAND_RSA_PRIVATE_KEY.

    Returns the first *existing* candidate; if none exist but one was
    specified, returns it anyway so the caller's error can name the
    missing path (e.g. "insert the USB"). None only if nothing was given.
    """
    candidates: list[pathlib.Path] = []
    if getattr(args, "private_key", None):
        candidates.append(pathlib.Path(args.private_key))
    env = os.getenv("FISHRAND_RSA_PRIVATE_KEY")
    if env:
        candidates.append(pathlib.Path(env))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0] if candidates else None


def cmd_keygen(args: argparse.Namespace) -> int:
    """Generate the long-term RSA-3072 keypair.

    Never called implicitly by encrypt/decrypt - a missing private key at
    decrypt time must fail safely, not trigger key generation.
    """
    from fishrand.rsa_hybrid import generate_keypair, serialize_private_key, serialize_public_key

    out_dir = pathlib.Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    priv_path = out_dir / PRIVATE_KEY_FILENAME
    pub_path = out_dir / PUBLIC_KEY_FILENAME
    if not args.force and (priv_path.exists() or pub_path.exists()):
        raise SystemExit(
            f"[keygen] {priv_path} or {pub_path} already exists — pass --force to overwrite"
        )

    passphrase: bytes | None = None
    if not args.no_passphrase:
        if args.passphrase_env:
            value = os.getenv(args.passphrase_env)
            if not value:
                raise SystemExit(f"[keygen] --passphrase-env {args.passphrase_env} is not set")
            passphrase = value.encode("utf-8")
        else:
            import getpass

            entered = getpass.getpass("Private key passphrase (leave blank for none): ")
            passphrase = entered.encode("utf-8") if entered else None

    private_key, public_key = generate_keypair()
    priv_path.write_bytes(serialize_private_key(private_key, passphrase))
    pub_path.write_bytes(serialize_public_key(public_key))
    # The private key is the whole secret - don't leave it world-readable.
    os.chmod(priv_path, 0o600)
    os.chmod(out_dir, 0o700)
    print(f"\n[keygen] RSA-3072 keypair written -> {out_dir}")
    print(f"[keygen] public key  (safe to keep on this machine):   {pub_path}")
    print(f"[keygen] private key (move to your USB, never commit): {priv_path}  [mode 600]")
    print(
        "[keygen] private key is passphrase-protected"
        if passphrase
        else "[keygen] private key has NO passphrase — anyone with the file can decrypt"
    )
    return 0


def _collect_audio_or_warn(args: argparse.Namespace) -> dict | None:
    """Capture one ESP32 audio window if a serial port is configured.

    Returns None (never raises) if no port is configured or the ESP32 is
    unavailable - callers treat that as "no audio for this session," which
    encrypt_with_observation() turns into a clear error only if the fish
    quality actually required audio.
    """
    from server import config as server_config

    port = args.audio_port or server_config.AUDIO_SERIAL_PORT
    if not port:
        return None

    from server.audio_serial import ESP32SerialReader, SerialReaderConfig, SerialReaderError, to_audio_observation

    cfg = SerialReaderConfig(
        port=port,
        baud_rate=server_config.AUDIO_BAUD_RATE,
        window_duration_s=server_config.AUDIO_WINDOW_DURATION_S,
        min_readings=server_config.AUDIO_MIN_READINGS,
    )
    print(
        f"[audio] capturing {cfg.window_duration_s}s of Sound_Level from {port} "
        f"@ {cfg.baud_rate} baud (close the Arduino IDE Serial Monitor first) ..."
    )
    try:
        with ESP32SerialReader(cfg) as reader:
            readings = reader.read_window()
    except SerialReaderError as exc:
        print(f"[audio] ESP32 unavailable ({exc}); continuing without audio", file=sys.stderr)
        return None
    print(f"[audio] collected {len(readings)} Sound_Level reading(s)")
    return to_audio_observation(readings, window_duration_s=cfg.window_duration_s)


def _collect_or_die() -> tuple[dict, str]:
    from server.collector import FishSourceError, collect_fish_verbose

    print("[collect] fetching fish observations via configured sources ...")
    try:
        return collect_fish_verbose()
    except FishSourceError as exc:
        raise SystemExit(f"[collect] no fish source available: {exc}")


def cmd_encrypt(args: argparse.Namespace) -> int:
    from fishrand.rsa_hybrid import load_public_key

    if args.fish:
        data = load_observations(args.fish)
        source = f"file:{args.fish}"
    else:
        data, source = _collect_or_die()
    if args.diary:
        plaintext = pathlib.Path(args.diary).read_text(encoding="utf-8")
    elif args.text is not None:
        plaintext = args.text
    else:
        raise SystemExit("provide --diary <path> or --text <inline plaintext>")

    try:
        public_key = load_public_key(args.public_key)
    except (FileNotFoundError, ValueError) as exc:
        raise SystemExit(f"[encrypt] {exc}")

    audio_data = None if args.no_audio else _collect_audio_or_warn(args)

    try:
        package = encrypt_with_observation(
            data, plaintext, public_key=public_key, audio_observations=audio_data
        )
    except ValueError as exc:
        raise SystemExit(f"[encrypt] {exc}")

    package["metadata"]["fish_source"] = source
    out_path = args.out or "encrypted.pkg"
    save_package(package, out_path)
    mode = package["metadata"].get("observation_mode")
    print(f"\n[package] written -> {out_path}  [mode 600]")
    print(f"[package] flavor: v4 · RSA-OAEP hybrid · observation: {mode}")
    print("[verify] no in-memory round-trip: this mode only holds the public key here")
    return 0


def cmd_decrypt(args: argparse.Namespace) -> int:
    from fishrand.rsa_hybrid import PrivateKeyNotFound, load_private_key

    pkg = load_package(args.pkg)

    key_path = _resolve_private_key_path(args)
    if key_path is None:
        raise SystemExit(
            "no private key specified — pass --private-key <path> or set FISHRAND_RSA_PRIVATE_KEY"
        )
    passphrase = None
    if getattr(args, "key_passphrase_env", None):
        value = os.getenv(args.key_passphrase_env)
        passphrase = value.encode("utf-8") if value else None
    try:
        private_key = load_private_key(key_path, passphrase)
    except PrivateKeyNotFound as exc:
        print(f"\n[decrypt] {exc}", file=sys.stderr)
        return 1

    try:
        plaintext = decrypt_rsa_hybrid_package(pkg, private_key=private_key)
    except AuthenticationFailure as exc:
        print(f"\n[decrypt] REJECTED: {exc}", file=sys.stderr)
        return 1

    out = args.out or "-"
    if out == "-":
        print("\n[DECRYPTED — AUTHENTICATED] (v4 · RSA-OAEP hybrid)")
        print("-----------------------------")
        sys.stdout.write(plaintext.decode("utf-8"))
        print("\n-----------------------------")
    else:
        pathlib.Path(out).write_bytes(plaintext)
        print(f"[decrypt] authenticated plaintext -> {out}")
    return 0


def cmd_keyinfo(args: argparse.Namespace) -> int:
    from fishrand.observe import observation_stats
    from fishrand.schema import validate_observations

    data = load_observations(args.fish)
    print(json.dumps(observation_stats(validate_observations(data)).to_dict(), indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fishrand", description="FISHRAND crypto pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    kg = sub.add_parser("keygen", help="generate the RSA-3072 keypair (do this first)")
    kg.add_argument("--output", default="./keys", help="directory to write private_key.pem/public_key.pem into")
    kg.add_argument("--no-passphrase", action="store_true",
                    help="write the private key unencrypted (fine for a local demo)")
    kg.add_argument("--passphrase-env", default=None,
                    help="env var holding the private key passphrase (non-interactive)")
    kg.add_argument("--force", action="store_true", help="overwrite an existing keypair in --output")
    kg.set_defaults(func=cmd_keygen)

    enc = sub.add_parser("encrypt", help="encrypt a diary for your RSA public key")
    enc.add_argument("--public-key", required=True, help="RSA public key PEM (from keygen)")
    enc.add_argument("--fish", default=None,
                     help="path to fish observations JSON (default: server collector)")
    enc.add_argument("--diary", help="path to plaintext file")
    enc.add_argument("--text", help="inline plaintext")
    enc.add_argument("--out", default=None, help="output package path (default encrypted.pkg)")
    enc.add_argument("--audio-port", default=None,
                     help="ESP32 serial port for live mic audio (e.g. /dev/ttyUSB0, /dev/ttyACM0); "
                          "default: FISHRAND_AUDIO_PORT env var. Close the Arduino IDE Serial "
                          "Monitor first — it and this CLI cannot hold the port open at once.")
    enc.add_argument("--no-audio", action="store_true",
                     help="skip ESP32 audio capture entirely; encryption fails clearly if the "
                          "fish quality turns out to require audio (MEDIUM/BAD)")
    enc.set_defaults(func=cmd_encrypt)

    dec = sub.add_parser("decrypt", help="decrypt a package with your RSA private key")
    dec.add_argument("--pkg", required=True, help="path to encrypted package JSON")
    dec.add_argument("--private-key", default=None,
                     help="RSA private key PEM (default: FISHRAND_RSA_PRIVATE_KEY)")
    dec.add_argument("--key-passphrase-env", default=None,
                     help="env var holding the RSA private key passphrase, if any")
    dec.add_argument("--out", default="-", help="output path or - for stdout")
    dec.set_defaults(func=cmd_decrypt)

    kinfo = sub.add_parser("keyinfo", help="print derived observation metadata")
    kinfo.add_argument("--fish", required=True, help="path to fish observations JSON")
    kinfo.set_defaults(func=cmd_keyinfo)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
