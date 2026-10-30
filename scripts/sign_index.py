"""Sign and verify ``index.json`` with Ed25519 (docs/marketplace-plan.md §4, decision D3).

The signature is over the exact bytes of ``index.json``; ``index.json.sig`` is a small JSON
document (``saengine.packageIndexSignature/1``) with the key id (the first 16 hex digits of the
SHA-256 of the raw 32-byte public key) and the 64-byte signature in base64. The app verifies it
with Node's ``crypto.verify(null, data, publicKey, signature)``.

The owner's key signs releases. Its private half never leaves the owner's offline machine and is
never committed; its public half is ``keys/index-public.pem``. Two ways to sign:

    # with this script (needs the 'cryptography' package)
    python scripts/sign_index.py sign index.json --key D:/offline/saengine-index-signing.pem

    # or with OpenSSL 3 only, then wrap (the wrap step checks the signature)
    openssl pkeyutl -sign -rawin -inkey D:/offline/saengine-index-signing.pem \
        -in index.json -out index.json.raw-sig
    python scripts/sign_index.py wrap index.json --raw index.json.raw-sig \
        --public keys/index-public.pem

    python scripts/sign_index.py verify index.json --public keys/index-public.pem

TEST KEY: ``--test-key`` signs with a key derived from a public seed (:data:`TEST_SEED`), so
anyone can recompute it: it proves nothing about who signed and release builds of SAEngine do
not accept it. CI and development builds use it. Its public half is
``keys/TEST-KEY-not-trusted-by-release-builds.pub.pem`` (``test-key`` writes it).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

SIGNATURE_SCHEMA = "saengine.packageIndexSignature/1"
# TEST KEY - not trusted by release builds: the private key is SHA-256 of this public text.
TEST_SEED = b"saengine-index TEST KEY - not trusted by release builds"
TEST_PUBLIC_KEY = "keys/TEST-KEY-not-trusted-by-release-builds.pub.pem"


def test_private_key() -> Ed25519PrivateKey:
    """TEST KEY - not trusted by release builds (derived from a published seed)."""
    return Ed25519PrivateKey.from_private_bytes(hashlib.sha256(TEST_SEED).digest())


def raw_public(key: Ed25519PublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def key_id(key: Ed25519PublicKey) -> str:
    return hashlib.sha256(raw_public(key)).hexdigest()[:16]


def public_pem(key: Ed25519PublicKey) -> str:
    return key.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode("ascii")


def load_public(path: Path) -> Ed25519PublicKey:
    key = serialization.load_pem_public_key(path.read_bytes())
    if not isinstance(key, Ed25519PublicKey):
        raise SystemExit(f"{path} is not an Ed25519 public key")
    return key


def load_private(path: Path) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise SystemExit(f"{path} is not an Ed25519 private key")
    return key


def signature_document(public: Ed25519PublicKey, signature: bytes) -> str:
    doc = {
        "schema": SIGNATURE_SCHEMA,
        "algorithm": "Ed25519",
        "keyId": key_id(public),
        "signature": base64.b64encode(signature).decode("ascii"),
    }
    return json.dumps(doc, indent=2) + "\n"


def verify(index: Path, sig: Path, public: Ed25519PublicKey) -> str | None:
    """None when ``sig`` is a valid signature of ``index`` by ``public``, else why not."""
    try:
        doc = json.loads(sig.read_text(encoding="utf-8"))
        if doc.get("schema") != SIGNATURE_SCHEMA or doc.get("algorithm") != "Ed25519":
            return "not a saengine.packageIndexSignature/1 Ed25519 signature"
        if doc.get("keyId") != key_id(public):
            return f"signed with key {doc.get('keyId')}, not {key_id(public)}"
        public.verify(base64.b64decode(doc["signature"], validate=True), index.read_bytes())
    except (OSError, ValueError, KeyError) as e:
        return f"cannot be read: {e}"
    except InvalidSignature:
        return "the signature does not match index.json"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("sign", help="sign index.json, writing index.json.sig")
    s.add_argument("index", type=Path)
    who = s.add_mutually_exclusive_group(required=True)
    who.add_argument("--key", type=Path, help="the owner's Ed25519 private key (PEM)")
    who.add_argument("--test-key", action="store_true", help="the TEST KEY (not trusted)")
    w = sub.add_parser("wrap", help="wrap a raw signature from openssl into index.json.sig")
    w.add_argument("index", type=Path)
    w.add_argument("--raw", type=Path, required=True)
    w.add_argument("--public", type=Path, required=True)
    v = sub.add_parser("verify", help="check index.json.sig")
    v.add_argument("index", type=Path)
    v.add_argument("--public", type=Path, required=True)
    v.add_argument("--sig", type=Path)
    t = sub.add_parser("test-key", help="write the TEST KEY's public key")
    t.add_argument(
        "--out", type=Path, default=Path(__file__).resolve().parents[1] / TEST_PUBLIC_KEY
    )
    args = parser.parse_args(argv)

    if args.command == "test-key":
        args.out.write_text(public_pem(test_private_key().public_key()), encoding="ascii")
        print(f"{args.out}: TEST KEY {key_id(test_private_key().public_key())}")
        return 0
    sig_path = Path(f"{args.index}.sig")
    if args.command == "sign":
        private = test_private_key() if args.test_key else load_private(args.key)
        public = private.public_key()
        sig_path.write_text(
            signature_document(public, private.sign(args.index.read_bytes())), encoding="ascii"
        )
        label = "TEST KEY (not trusted by release builds)" if args.test_key else "key"
        print(f"{sig_path}: signed with {label} {key_id(public)}")
        return 0
    public = load_public(args.public)
    if args.command == "wrap":
        sig_path.write_text(signature_document(public, args.raw.read_bytes()), encoding="ascii")
    sig = getattr(args, "sig", None) or sig_path
    problem = verify(args.index, sig, public)
    if problem:
        print(f"{sig}: NOT VALID: {problem}", file=sys.stderr)
        return 1
    print(f"{sig}: valid, key {key_id(public)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
