# Keys

Only **public** keys are ever committed here.

| File | Key id | Trusted by |
| --- | --- | --- |
| `TEST-KEY-not-trusted-by-release-builds.pub.pem` | `82af6b8cd5077d21` | CI and development builds only. **TEST KEY - not trusted by release builds.** Its private key is the SHA-256 of a seed published in `scripts/sign_index.py`, so anyone can sign with it: it proves nothing about who signed. |
| `index-public.pem` | `79cc259c3d2aa2c7` | Release builds of SAEngine (added 2026-11-02). |

The key id is the first 16 hex digits of the SHA-256 of the raw 32-byte Ed25519 public key; it
is in every `index.json.sig`, and `sign_index.py` prints it.

## The owner's key (round M0)

Create it on an offline machine, keep the private key there with a backup, never commit or
upload it:

```console
openssl genpkey -algorithm ed25519 -out saengine-index-signing.pem
openssl pkey -in saengine-index-signing.pem -pubout -out index-public.pem
```

Then commit only `index-public.pem` here (a pull request), and give the same public key to
SAEngine, where the app (round M3) ships it and release builds accept only it. Rotating the key
means a new public key here and in an app release.
