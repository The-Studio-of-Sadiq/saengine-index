"""Build the assets of a package release: one zip per new package version and ``packages.json``.

Run by a package repository's publish workflow on a ``packages-*`` tag, after
``check_packages.py --all``. For every package (a top-level folder with ``pack.json``) whose
``<id>@<version>`` is not in an earlier release of the repository it writes, into ``--out``:

* ``<id>-<version>.zip``: the package folder under ``<id>/``, reproducible (sorted entries, fixed
  times and permissions), without Python caches: the file ``Registry.install()`` takes;
* ``<id>-<version>.preview.<ext>``: the card image, when the package has one;
* ``packages.json`` (``saengine.packageRelease/1``, scripts/index_model.py): each package's
  facts, size and SHA-256, and the hash of its hooks as SAEngine's trust store computes it.

A version that is already published with other content is an error: published versions never
change (the index pins them by hash). Nothing new to publish is not an error: ``packages.json``
then lists no package and the workflow creates no release.

    python scripts/package_release.py --repo . --kind designCode \
        --repository The-Studio-of-Sadiq/saengine-design-codes --tag packages-2026-11-15 \
        --saengine ../SAEngine --out dist [--previous prev/*/packages.json]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
from index_model import PackageRelease, ReleasePackage, Requires, dump  # noqa: E402

MANIFEST = "pack.json"
ZIP_TIME = (1980, 1, 1, 0, 0, 0)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _skipped(rel: PurePosixPath) -> bool:
    return "__pycache__" in rel.parts or rel.suffix.lower() in (".pyc", ".pyo")


def package_files(folder: Path) -> list[tuple[str, Path]]:
    """The package's files (relative POSIX path, path), sorted."""
    files = []
    for path in folder.rglob("*"):
        rel = PurePosixPath(path.relative_to(folder).as_posix())
        if path.is_file() and not path.is_symlink() and not _skipped(rel):
            files.append((str(rel), path))
    return sorted(files)


def write_zip(folder: Path, pack_id: str, dest: Path) -> None:
    """The package as ``<id>/...``, byte for byte the same for the same files."""
    with zipfile.ZipFile(dest, "w") as zf:
        for rel, path in package_files(folder):
            info = zipfile.ZipInfo(f"{pack_id}/{rel}", date_time=ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            info.create_system = 3  # unix, so the permissions above are read as such
            zf.writestr(info, path.read_bytes(), compresslevel=9)


def hooks_hash(saengine_src: Path | None, folder: Path, hooks: str) -> str:
    """The code hash SAEngine's trust store computes (saengine.codes.trust.code_hash)."""
    if saengine_src is not None and str(saengine_src) not in sys.path:
        sys.path.insert(0, str(saengine_src))
    from saengine.codes.trust import code_hash

    return code_hash(folder, hooks)


def published(previous: list[Path]) -> dict[tuple[str, str], tuple[str, str]]:
    """``(id, version) -> (sha256, tag)`` of the earlier releases."""
    found = {}
    for path in previous:
        release = PackageRelease.model_validate_json(path.read_text(encoding="utf-8"))
        for p in release.packages:
            found[(p.id, p.version)] = (p.sha256, release.tag)
    return found


def build(
    repo: Path,
    kind: str,
    repository: str,
    tag: str,
    out: Path,
    saengine_src: Path | None = None,
    previous: list[Path] | None = None,
) -> PackageRelease:
    out.mkdir(parents=True, exist_ok=True)
    earlier = published(previous or [])
    packages: list[ReleasePackage] = []
    errors: list[str] = []
    for folder in sorted(p for p in repo.iterdir() if (p / MANIFEST).is_file()):
        if folder.name.startswith("."):
            continue
        m = json.loads((folder / MANIFEST).read_text(encoding="utf-8-sig"))
        pack_id, version = m["id"], str(m.get("version", "1.0.0"))
        if m.get("kind", "designCode") != kind:
            errors.append(f"{pack_id}: a {m.get('kind', 'designCode')} in a {kind} repository")
            continue
        name = f"{pack_id}-{version}.zip"
        zip_path = out / name
        write_zip(folder, pack_id, zip_path)
        digest = sha256_file(zip_path)
        if (pack_id, version) in earlier:
            old, old_tag = earlier[(pack_id, version)]
            zip_path.unlink()
            if old != digest:
                errors.append(
                    f"{pack_id} {version} was published in {old_tag} with other content: "
                    "raise its version"
                )
            continue
        preview_name, preview_sha = None, None
        if m.get("preview"):
            source = folder / m["preview"]
            preview_name = f"{pack_id}-{version}.preview{source.suffix.lower()}"
            shutil.copyfile(source, out / preview_name)
            preview_sha = sha256_file(source)
        hooks = m.get("hooks")
        packages.append(
            ReleasePackage(
                id=pack_id,
                kind=kind,
                name=m["name"],
                version=version,
                country=m.get("country"),
                region=m.get("region"),
                author=m.get("author"),
                licence=m["licence"],
                description=m.get("description"),
                requires=Requires.model_validate(m["requires"]) if m.get("requires") else None,
                file=name,
                size=zip_path.stat().st_size,
                sha256=digest,
                hooks=bool(hooks),
                hooksSha256=hooks_hash(saengine_src, folder, hooks) if hooks else None,
                preview=preview_name,
                previewSha256=preview_sha,
            )
        )
    if errors:
        raise SystemExit("\n".join(errors))
    release = PackageRelease(repository=repository, tag=tag, kind=kind, packages=packages)
    (out / "packages.json").write_text(dump(release), encoding="utf-8", newline="\n")
    return release


def notes(release: PackageRelease) -> str:
    """The release's description."""
    lines = ["Packages in this release (sizes and SHA-256 in packages.json):", ""]
    lines += [f"- {p.name} (`{p.id}`) {p.version}, {p.licence}" for p in release.packages]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--kind")
    parser.add_argument("--repository", help="owner/name on GitHub")
    parser.add_argument("--tag")
    parser.add_argument("--out", type=Path)
    parser.add_argument(
        "--saengine", type=Path, help="SAEngine checkout (for the hooks hash of packages with code)"
    )
    parser.add_argument(
        "--previous", type=Path, nargs="*", default=[], help="earlier packages.json"
    )
    parser.add_argument("--count", type=Path, help="print 'count=N' of a packages.json and stop")
    parser.add_argument("--notes", type=Path, help="print the release notes of a packages.json")
    args = parser.parse_args(argv)
    if args.count or args.notes:
        path = args.count or args.notes
        release = PackageRelease.model_validate_json(path.read_text(encoding="utf-8"))
        print(
            f"count={len(release.packages)}" if args.count else notes(release),
            end="\n" if args.count else "",
        )
        return 0
    missing = [n for n in ("kind", "repository", "tag", "out") if getattr(args, n) is None]
    if missing:
        parser.error(f"needs --{', --'.join(missing)}")
    src = args.saengine / "python" / "src" if args.saengine else None
    release = build(
        args.repo.resolve(), args.kind, args.repository, args.tag, args.out, src, args.previous
    )
    for p in release.packages:
        print(f"{p.file}  {p.size} bytes  sha256 {p.sha256}")
    if not release.packages:
        print("nothing new to publish")
    return 0


if __name__ == "__main__":
    sys.exit(main())
