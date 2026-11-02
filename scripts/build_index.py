"""Build ``index.json``: every package version the official repositories have released.

For each repository of ``sources.json`` it reads every published release whose tag matches the
source's ``tagPattern``, takes the release's ``packages.json`` (written by
``package_release.py``), downloads each zip and preview, checks its size and SHA-256 against
that manifest, and writes one entry per package version with the asset URLs
(``saengine.packageIndex/1``, scripts/index_model.py). Then ``sign_index.py`` signs the bytes
of ``index.json``.

Refused: a release whose kind or repository is not its source's, a package of another kind, a
package id published by two repositories (ids are one namespace), one version released twice,
an asset that does not match its size or hash, a URL that is not HTTPS on GitHub.

    python scripts/build_index.py --out dist/index.json
    python scripts/build_index.py --out dist/index.json \
        --local The-Studio-of-Sadiq/saengine-design-codes=dry-run/design-codes

``--local REPOSITORY=FOLDER`` reads that repository's release from a folder (its
``packages.json`` and assets, as ``package_release.py`` wrote them) instead of GitHub: the dry
run of a release that does not exist yet. The URLs written are the ones the release will have.
``--offline`` skips the repositories without ``--local``. ``GITHUB_TOKEN`` is used when set.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from index_model import (  # noqa: E402
    IndexPackage,
    PackageIndex,
    PackageRelease,
    Source,
    Sources,
    dump,
)

API = "https://api.github.com"
ALLOWED_HOSTS = frozenset(
    {
        "api.github.com",
        "github.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
    }
)
MAX_ASSET_BYTES = 60 * 1024 * 1024


class BuildError(Exception):
    pass


def _get(url: str, accept: str = "application/octet-stream") -> bytes:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS:
        raise BuildError(f"not fetched: {url} is not HTTPS on GitHub")
    request = urllib.request.Request(
        url, headers={"Accept": accept, "User-Agent": "saengine-index"}
    )
    token = os.environ.get("GITHUB_TOKEN")
    if token and parts.hostname == "api.github.com":
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 (checked above)
        data = response.read(MAX_ASSET_BYTES + 1)
    if len(data) > MAX_ASSET_BYTES:
        raise BuildError(f"{url} is larger than {MAX_ASSET_BYTES // 2**20} MB")
    return data


def asset_url(repository: str, tag: str, name: str) -> str:
    q = urllib.parse.quote
    return f"https://github.com/{repository}/releases/download/{q(tag)}/{q(name)}"


class GitHubReleases:
    """The published releases of a repository and their assets, from the GitHub API."""

    def __init__(self, source: Source) -> None:
        self.source = source

    def releases(self) -> list[tuple[str, dict[str, str]]]:
        """(tag, {asset name: download URL}) of every published release that matches."""
        found, page = [], 1
        while True:
            url = f"{API}/repos/{self.source.repository}/releases?per_page=100&page={page}"
            batch = json.loads(_get(url, "application/vnd.github+json"))
            for r in batch:
                if r.get("draft") or r.get("prerelease"):
                    continue
                if fnmatch.fnmatchcase(r["tag_name"], self.source.tagPattern):
                    assets = {a["name"]: a["browser_download_url"] for a in r.get("assets", [])}
                    found.append((r["tag_name"], assets))
            if len(batch) < 100:
                return found
            page += 1

    def read(self, tag: str, assets: dict[str, str], name: str) -> bytes:
        if name not in assets:
            raise BuildError(f"{self.source.repository} {tag}: no asset {name}")
        return _get(assets[name])


class LocalRelease:
    """A release not published yet: the folder package_release.py wrote."""

    def __init__(self, source: Source, folder: Path) -> None:
        self.source, self.folder = source, folder

    def releases(self) -> list[tuple[str, dict[str, str]]]:
        manifest = PackageRelease.model_validate_json(
            (self.folder / "packages.json").read_text(encoding="utf-8")
        )
        return [(manifest.tag, {p.name: str(p) for p in self.folder.iterdir() if p.is_file()})]

    def read(self, tag: str, assets: dict[str, str], name: str) -> bytes:
        if name not in assets:
            raise BuildError(f"{self.source.repository} {tag} (local): no file {name}")
        return Path(assets[name]).read_bytes()


def _checked(data: bytes, size: int | None, sha256: str, what: str) -> None:
    if size is not None and len(data) != size:
        raise BuildError(f"{what}: {len(data)} bytes, the release says {size}")
    if hashlib.sha256(data).hexdigest() != sha256:
        raise BuildError(f"{what}: SHA-256 does not match the release's packages.json")


def build(
    sources: Sources,
    local: dict[str, Path] | None = None,
    offline: bool = False,
    generated: str | None = None,
) -> PackageIndex:
    local = local or {}
    entries: list[IndexPackage] = []
    owner: dict[str, str] = {}  # package id -> repository
    seen: dict[tuple[str, str], str] = {}  # (id, version) -> tag
    for source in sources.sources:
        if source.repository in local:
            reader: GitHubReleases | LocalRelease = LocalRelease(source, local[source.repository])
        elif offline:
            print(f"{source.repository}: skipped (offline)")
            continue
        else:
            reader = GitHubReleases(source)
        for tag, assets in reader.releases():
            raw = reader.read(tag, assets, "packages.json")
            release = PackageRelease.model_validate_json(raw)
            where = f"{source.repository} {tag}"
            if release.repository != source.repository or release.tag != tag:
                raise BuildError(f"{where}: packages.json names {release.repository} {release.tag}")
            if release.kind != source.kind:
                raise BuildError(f"{where}: a {release.kind} release from a {source.kind} source")
            for p in release.packages:
                if p.kind != source.kind:
                    raise BuildError(f"{where}: {p.id} is a {p.kind}, not a {source.kind}")
                if owner.setdefault(p.id, source.repository) != source.repository:
                    raise BuildError(f"{where}: {p.id} is already published by {owner[p.id]}")
                earlier = seen.get((p.id, p.version))
                if earlier:
                    raise BuildError(
                        f"{where}: {p.id} {p.version} was released before, in {earlier}"
                    )
                seen[(p.id, p.version)] = tag
                _checked(reader.read(tag, assets, p.file), p.size, p.sha256, f"{where} {p.file}")
                preview_url = None
                if p.preview:
                    if not p.previewSha256:
                        raise BuildError(f"{where}: {p.preview} has no SHA-256")
                    data = reader.read(tag, assets, p.preview)
                    _checked(data, None, p.previewSha256, f"{where} {p.preview}")
                    preview_url = asset_url(source.repository, tag, p.preview)
                facts = p.model_dump(exclude={"file", "preview"})
                entries.append(
                    IndexPackage(
                        **facts,
                        url=asset_url(source.repository, tag, p.file),
                        preview=preview_url,
                        source=source.source,
                        repository=source.repository,
                        tag=tag,
                    )
                )
    entries.sort(key=lambda e: (e.kind, e.id, _version_key(e.version)))
    stamp = generated or datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return PackageIndex(generated=stamp, packages=entries)


def _version_key(version: str) -> tuple:
    return tuple(int(x) if x.isdigit() else -1 for x in version.split("-")[0].split("."))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--sources", type=Path, default=root / "sources.json")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--local", action="append", default=[], metavar="REPOSITORY=FOLDER")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--generated", help="the 'generated' time (reproducible builds)")
    args = parser.parse_args(argv)
    local = {}
    for item in args.local:
        repository, sep, folder = item.partition("=")
        if not sep:
            parser.error(f"--local {item}: write REPOSITORY=FOLDER")
        local[repository] = Path(folder)
    sources = Sources.model_validate_json(args.sources.read_text(encoding="utf-8"))
    unknown = set(local) - {s.repository for s in sources.sources}
    if unknown:
        parser.error(f"--local names repositories that are not sources: {', '.join(unknown)}")
    try:
        index = build(sources, local, args.offline, args.generated)
    except BuildError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(dump(index), encoding="utf-8", newline="\n")
    print(f"{args.out}: {len(index.packages)} package versions")
    for e in index.packages:
        print(f"  {e.kind:18} {e.id} {e.version}  {e.size} bytes  {e.url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
