"""FISHRAND CLI - encrypt/decrypt demo via the terminal.

The universal USB code story:

  1. Create the code once on your USB stick
        python -m fishrand.cli init --dir /media/USB
  2. Encrypt a diary — the fish is collected automatically, the code is read
     from the USB, and the package contains NO secret
        python -m fishrand.cli encrypt --diary examples/diary.txt \
            --out /media/USB/diary.pkg
  3. On another laptop, unlock with the same USB stick
        python -m fishrand.cli decrypt --usb /media/USB --pkg /media/USB/diary.pkg

The code can unlock ANY packaged message the fish encrypted. Without it the
package is just opaque ciphertext. (Packages from the old v1 demo, which
embedded the randomness, still decrypt without a code.)
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

from fishrand import (
    AuthenticationFailure,
    decrypt_message,
    decrypt_package,
    decrypt_rsa_hybrid_package,
    encrypt_message,
    encrypt_with_fish_entropy,
    encrypt_with_rsa_hybrid,
    load_package,
    load_observations,
    save_package,
)

CODE_FILENAME = "code.txt"
PRIVATE_KEY_FILENAME = "private_key.pem"


def _resolve_code(args: argparse.Namespace) -> str | None:
    """Locate the universal USB code (code.txt) and return its text.

    Search order: --code <file>, --usb <dir>/code.txt, FISHRAND_USB_CODE,
    ./code.txt in the current directory.
    """
    candidates: list[pathlib.Path] = []
    if getattr(args, "code", None):
        candidates.append(pathlib.Path(args.code))
    if getattr(args, "usb", None):
        candidates.append(pathlib.Path(args.usb) / CODE_FILENAME)
    env = os.getenv("FISHRAND_USB_CODE")
    if env:
        candidates.append(pathlib.Path(env))
    candidates.append(pathlib.Path(CODE_FILENAME))
    for candidate in candidates:
        if candidate.exists():
            return candidate.read_text(encoding="utf-8").strip() or None
    return None


def _resolve_private_key_path(args: argparse.Namespace) -> pathlib.Path | None:
    """Locate the RSA private key file for v4 (RSA-OAEP hybrid) decryption.

    Search order: --private-key <file>, --usb <dir>/fishrand/private_key.pem,
    FISHRAND_RSA_PRIVATE_KEY. Returns the first *existing* candidate; if
    none exist but at least one was specified, returns that one anyway so
    the caller's error message can name the missing path (e.g. "insert the
    USB"). Returns None only if nothing was specified at all.
    """
    candidates: list[pathlib.Path] = []
    if getattr(args, "private_key", None):
        candidates.append(pathlib.Path(args.private_key))
    if getattr(args, "usb", None):
        candidates.append(pathlib.Path(args.usb) / "fishrand" / PRIVATE_KEY_FILENAME)
    env = os.getenv("FISHRAND_RSA_PRIVATE_KEY")
    if env:
        candidates.append(pathlib.Path(env))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0] if candidates else None


def cmd_keygen(args: argparse.Namespace) -> int:
    """Generate the long-term RSA-3072 keypair for the hybrid mode.

    Never called implicitly by encrypt/decrypt — a missing private key at
    decrypt time must fail safely, not trigger key generation.
    """
    from fishrand.rsa_hybrid import generate_keypair, serialize_private_key, serialize_public_key

    out_dir = pathlib.Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    priv_path = out_dir / PRIVATE_KEY_FILENAME
    pub_path = out_dir / "public_key.pem"
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
    print(f"\n[keygen] RSA-3072 keypair written -> {out_dir}")
    print(f"[keygen] public key  (ship with the app):        {pub_path}")
    print(f"[keygen] private key (move to your USB, never commit): {priv_path}")
    print(
        "[keygen] private key is passphrase-protected"
        if passphrase
        else "[keygen] private key has NO passphrase — anyone with the file can decrypt"
    )
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    from fishrand.cspng import generate_usb_code

    out_dir = pathlib.Path(args.dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / CODE_FILENAME
    out.write_text(generate_usb_code() + "\n", encoding="utf-8")
    print(f"\n[init] universal USB code written -> {out}")
    print("[init] this code can unlock ANY message the fish encrypts. Keep the USB safe.")
    return 0


def cmd_encrypt(args: argparse.Namespace) -> int:
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

    if args.public_key:
        from fishrand.rsa_hybrid import load_public_key

        public_key = load_public_key(args.public_key)
        package = encrypt_with_rsa_hybrid(data, plaintext, public_key=public_key)
        if args.fish is None:
            package["metadata"]["fish_source"] = source
        out_path = args.out or "encrypted.pkg"
        save_package(package, out_path)
        print(f"\n[package] written -> {out_path}")
        print("[package] flavor: v4 · RSA-OAEP hybrid (decrypt needs ONLY the RSA private key)")
        print("[verify] no in-memory round-trip: this mode only holds the public key here")
        return 0

    code = _resolve_code(args)
    if code is None and not args.demo:
        raise SystemExit(
            "no universal USB code found — run: fishrand.cli init --dir <your usb>  "
            "(or pass --demo for the self-contained v1 demo)"
        )
    session_code = None if args.demo else code
    package = encrypt_with_fish_entropy(data, plaintext, session_code=session_code)
    if args.fish is None:
        package["metadata"]["fish_observations"] = data
        package["metadata"]["fish_source"] = source
    out_path = args.out or "encrypted.pkg"
    save_package(package, out_path)
    print(f"\n[package] written -> {out_path}")
    print(f"[package] flavor: {'v2 · USB code (secret NOT in package)' if session_code else 'v1 · self-contained demo'}")
    pkg = load_package(out_path)
    # Prove the tag round-trips: decrypt in-memory with the same material.
    try:
        key = package_roundtrip_key(data, pkg, session_code)
        decrypt_message(key, pkg)
        print("[verify] in-memory decrypt round-trip: AUTH OK")
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[verify] in-memory decrypt skipped: {exc}")
    return 0


def _collect_or_die() -> tuple[dict, str]:
    from server.collector import FishSourceError, collect_fish_verbose

    print("[collect] fetching fish observations via configured sources ...")
    try:
        return collect_fish_verbose()
    except FishSourceError as exc:
        raise SystemExit(f"[collect] no fish source available: {exc}")


def package_roundtrip_key(data: dict, pkg: dict, session_code: str | None) -> bytes:
    """Re-derive the session key from fish data + package contents.

    Domain-separation info always comes from the fixed fishrand.mixing
    constants for the package's AUTHENTICATED version — never from the
    package JSON's own kdf_context field — so this self-check dispatches
    exactly the same way fishrand.api.decrypt_package() does.
    """
    from fishrand.cspng import usb_code_bytes
    from fishrand.entropy import fish_digest_bytes
    from fishrand.fishchain import fish_chain, fish_noise, fish_units
    from fishrand.mixing import DOMAIN_INFO, USB_CODE_INFO, derive_key
    from fishrand.package import parse_package

    parsed = parse_package(pkg)
    if parsed["os_random"] is not None:
        return derive_key(fish_digest_bytes(data), parsed["os_random"], info=DOMAIN_INFO)
    if not session_code:
        raise ValueError("v2/v3 package needs the USB code to re-derive the key")
    secret = usb_code_bytes(session_code)
    if parsed["version"] == 3:
        return fish_chain(secret, fish_units(data), noise=fish_noise(data))[0]
    return derive_key(fish_digest_bytes(data), secret, info=USB_CODE_INFO)


def cmd_decrypt(args: argparse.Namespace) -> int:
    pkg = load_package(args.pkg)

    if pkg.get("version") == 4:
        from fishrand.rsa_hybrid import PrivateKeyNotFound, load_private_key

        key_path = _resolve_private_key_path(args)
        if key_path is None:
            raise SystemExit(
                "v4 (RSA hybrid) package: no private key specified — pass --private-key <path>, "
                "--usb <dir>, or set FISHRAND_RSA_PRIVATE_KEY"
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

    if args.fish:
        data = load_observations(args.fish)
        source = f"override:{args.fish}"
    else:
        bound = pkg.get("metadata", {}).get("fish_observations")
        if bound is None:
            raise SystemExit("package has no bound fish observations; pass --fish <observations.json>")
        data = bound
        source = pkg.get("metadata", {}).get("fish_source", "package")
    code = _resolve_code(args)
    print(f"[collect] fish source: {source}")
    print(f"[collect] usb code: {'present (code.txt)' if code else 'none'}")
    try:
        plaintext = decrypt_package(data, pkg, session_code=code)
        p = args.out or "-"
        if p == "-":
            print("\n[DECRYPTED — AUTHENTICATED]")
            print("-----------------------------")
            sys.stdout.write(plaintext.decode("utf-8"))
            print("\n-----------------------------")
        else:
            pathlib.Path(p).write_bytes(plaintext)
            print(f"[decrypt] authenticated plaintext -> {p}")
    except AuthenticationFailure as exc:
        print(f"\n[decrypt] REJECTED: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_keyinfo(args: argparse.Namespace) -> int:
    data = load_observations(args.fish)
    from fishrand.observe import observation_stats

    print(json.dumps(observation_stats(validate(data)).to_dict(), indent=2))
    return 0


def validate(data: dict) -> dict:
    from fishrand.schema import validate_observations

    return validate_observations(data)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fishrand", description="FISHRAND crypto pipeline for the hackathon")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="create the universal USB code (code.txt)")
    init.add_argument("--dir", default=".", help="directory to write code.txt into (default: .)")
    init.set_defaults(func=cmd_init)

    kg = sub.add_parser("keygen", help="generate an RSA-3072 keypair for the hybrid encryption mode")
    kg.add_argument("--output", default="./keys", help="directory to write private_key.pem/public_key.pem into")
    kg.add_argument("--no-passphrase", action="store_true",
                    help="write the private key unencrypted (fine for a hackathon demo)")
    kg.add_argument("--passphrase-env", default=None,
                    help="env var holding the private key passphrase (non-interactive)")
    kg.add_argument("--force", action="store_true", help="overwrite an existing keypair in --output")
    kg.set_defaults(func=cmd_keygen)

    enc = sub.add_parser("encrypt", help="encrypt a diary with fish entropy")
    enc.add_argument("--fish", default=None,
                     help="path to fish observations JSON (default: server collector)")
    enc.add_argument("--diary", help="path to plaintext (default: encrypted.pkg)")
    enc.add_argument("--text", help="inline plaintext")
    enc.add_argument("--out", default=None, help="output package path (default encrypted.pkg)")
    enc.add_argument("--usb", default=None, help="mount point of your USB key (reads code.txt)")
    enc.add_argument("--code", default=None, help="explicit path to a code.txt file")
    enc.add_argument("--demo", action="store_true",
                     help="legacy v1 self-contained demo (no USB code needed)")
    enc.add_argument("--public-key", default=None,
                     help="RSA public key PEM — switches to v4 RSA-OAEP hybrid mode "
                          "(decrypt then needs ONLY the matching private key, no fish/code)")
    enc.set_defaults(func=cmd_encrypt)

    dec = sub.add_parser("decrypt", help="decrypt a package (re-derive session key)")
    dec.add_argument("--fish", default=None,
                     help="path to fish observations JSON (default: fish bound in the package)")
    dec.add_argument("--pkg", required=True, help="path to encrypted package JSON")
    dec.add_argument("--out", default="-", help="output path or - for stdout")
    dec.add_argument("--usb", default=None, help="mount point of your USB key (reads code.txt / fishrand/private_key.pem)")
    dec.add_argument("--code", default=None, help="explicit path to a code.txt file")
    dec.add_argument("--private-key", default=None,
                     help="RSA private key PEM — required for v4 (RSA hybrid) packages")
    dec.add_argument("--key-passphrase-env", default=None,
                     help="env var holding the RSA private key passphrase, if any")
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