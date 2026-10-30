# saengine-index

The signed package index the [SAEngine](https://github.com/The-Studio-of-Sadiq/SAEngine) app
reads: it merges the releases of the three package repositories,
[saengine-design-codes](https://github.com/The-Studio-of-Sadiq/saengine-design-codes),
[saengine-structure-templates](https://github.com/The-Studio-of-Sadiq/saengine-structure-templates)
and
[saengine-building-templates](https://github.com/The-Studio-of-Sadiq/saengine-building-templates),
into one `index.json`, signed with Ed25519. The design is SAEngine's
`docs/marketplace-plan.md` (§3 index and repositories, §4 trust).

| Path | What it is |
| --- | --- |
| `sources.json` | The package repositories the index merges (`saengine.packageSources/1`). Official only for now; community entries come after round M5 (decision D10). |
| `scripts/index_model.py` | The models of `sources.json`, a release's `packages.json` and `index.json`; `schemas/` holds their JSON Schemas (written by this script, checked by CI). |
| `scripts/check_packages.py` | The checks a package repository runs on each changed package: SAEngine's own validator (`saengine codepack validate --kind`), then SPDX licence, code licence for hooks, small previews, a CODEOWNERS entry for packages with hooks, a new version for a changed package, mirrors equal to their SAEngine commit. |
| `scripts/package_release.py` | Builds a release: reproducible `<id>-<version>.zip` for each version not released before, previews, `packages.json` with sizes and SHA-256. |
| `scripts/build_index.py` | Reads every release's `packages.json`, downloads each asset, checks its size and SHA-256, writes `index.json` (`saengine.packageIndex/1`). |
| `scripts/sign_index.py` | Signs and verifies `index.json` (`index.json.sig`, `saengine.packageIndexSignature/1`). |
| `.github/workflows/validate-packages.yml` | Reusable: the package repositories' pull request checks. |
| `.github/workflows/publish-packages.yml` | Reusable: the package repositories' tag release (or a dry run). |
| `.github/workflows/build-index.yml` | Builds `index.json` after a release (dispatch) or by hand, signs it with the TEST KEY, keeps it as an artifact. |
| `keys/` | Public keys. See `keys/README.md`. |

The workflows and scripts are defined once, here; the package repositories only call them.

## How a package reaches the app

1. A pull request in a package repository is checked (`validate-packages.yml`).
2. A maintainer tags `packages-YYYY-MM-DD` there: `publish-packages.yml` creates an immutable
   release with the zips, previews and `packages.json`, and dispatches `packages-released`
   here.
3. `build-index.yml` builds `index.json` and signs it with the TEST KEY (an artifact).
4. The owner signs `index.json` with the real key offline and publishes it ("Publishing the
   index" below). The app (round M3) fetches the latest `index-*` release of this repository,
   verifies `index.json.sig` with the public key it ships, and downloads a package only if its
   size and SHA-256 match the index.

## Publishing the index (owner)

```console
gh run download <run id> -R The-Studio-of-Sadiq/saengine-index -n index-test-signed -D out
python scripts/sign_index.py verify out/index.json --public keys/TEST-KEY-not-trusted-by-release-builds.pub.pem
# read out/index.json: every package, version, URL and hash is what you expect

# on the offline machine, with the private key:
python scripts/sign_index.py sign out/index.json --key <offline>/saengine-index-signing.pem
#   or OpenSSL 3 only:
#   openssl pkeyutl -sign -rawin -inkey <offline>/saengine-index-signing.pem -in out/index.json -out out/index.json.raw-sig
#   python scripts/sign_index.py wrap out/index.json --raw out/index.json.raw-sig --public keys/index-public.pem

python scripts/sign_index.py verify out/index.json --public keys/index-public.pem
gh release create index-YYYY-MM-DD -R The-Studio-of-Sadiq/saengine-index --title index-YYYY-MM-DD out/index.json out/index.json.sig
```

`index.json` must not be edited after signing (the signature is over its exact bytes).

## Dry run, locally

```console
python -m pip install -r requirements.txt
# in a package repository clone, with a SAEngine checkout at ../SAEngine
python ../saengine-index/scripts/check_packages.py --repo . --kind designCode --saengine ../SAEngine --all
python ../saengine-index/scripts/package_release.py --repo . --kind designCode \
    --repository The-Studio-of-Sadiq/saengine-design-codes --tag packages-dry-run \
    --saengine ../SAEngine --out ../dry-run/design-codes
# here
python scripts/build_index.py --out ../dry-run/index.json --offline \
    --local The-Studio-of-Sadiq/saengine-design-codes=../dry-run/design-codes
python scripts/sign_index.py sign ../dry-run/index.json --test-key
```

## Tests

`python -m pytest tests` (set `SAENGINE_CHECKOUT` to a SAEngine checkout for the end-to-end
check); `ruff check scripts tests`; `python scripts/index_model.py --check`.

Licence: AGPL-3.0-only (`LICENSE`), as SAEngine.
