"""The documents of the package index: their models, and their JSON Schemas in ``schemas/``.

* ``sources.json`` (:class:`Sources`, ``saengine.packageSources/1``): the package repositories
  the index merges;
* ``packages.json`` (:class:`PackageRelease`, ``saengine.packageRelease/1``): the manifest of one
  release of a package repository, written by ``package_release.py`` and attached to the release
  beside the package zips;
* ``index.json`` (:class:`PackageIndex`, ``saengine.packageIndex/1``): what the SAEngine app
  reads, written by ``build_index.py`` and signed (``sign_index.py``).

The package manifest (``pack.json``) is not defined here: it is SAEngine's
(``saengine.codes.manifest.PackManifest``), and the packages are validated by SAEngine's own
``saengine codepack validate``.

    python scripts/index_model.py            write schemas/*.schema.json
    python scripts/index_model.py --check    fail when they are out of date
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Kind = Literal["designCode", "structureTemplate", "buildingTemplate"]
SourceKind = Literal["official", "community"]

SHA256 = r"^[0-9a-f]{64}$"
PACKAGE_ID = r"^[a-z0-9][a-z0-9._-]{0,63}$"
REPOSITORY = r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$"
SCHEMAS_DIR = Path(__file__).resolve().parents[1] / "schemas"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Requires(_Model):
    """As ``pack.json`` ``requires``."""

    saengine: str | None = None
    packages: dict[str, str] = Field(default_factory=dict)


class Source(_Model):
    """A package repository whose releases the index merges."""

    repository: str = Field(pattern=REPOSITORY, description="GitHub owner/name.")
    kind: Kind = Field(description="The one kind of package the repository publishes.")
    source: SourceKind = "official"
    tagPattern: str = Field(
        default="packages-*", description="Glob of the release tags that publish packages."
    )


class Sources(_Model):
    schema_: Literal["saengine.packageSources/1"] = Field(
        default="saengine.packageSources/1", alias="schema"
    )
    sources: list[Source]


class _PackageFacts(_Model):
    """What the index repeats from ``pack.json``."""

    id: str = Field(pattern=PACKAGE_ID)
    kind: Kind
    name: str
    version: str
    country: str | None = None
    region: str | None = None
    author: str | None = None
    licence: str = Field(description="SPDX licence expression of the package.")
    description: str | None = None
    requires: Requires | None = None
    size: int = Field(ge=0, description="Size of the zip [bytes].")
    sha256: str = Field(pattern=SHA256, description="SHA-256 of the zip.")
    hooks: bool = Field(description="Whether the package holds Python hooks.")
    hooksSha256: str | None = Field(
        default=None,
        pattern=SHA256,
        description="The code hash SAEngine's trust store computes for the hooks "
        "(saengine.codes.trust.code_hash): the code the reviewers saw.",
    )
    previewSha256: str | None = Field(default=None, pattern=SHA256)


class ReleasePackage(_PackageFacts):
    """A package of one release: its files are assets of the same release."""

    file: str = Field(description="Asset name of the zip: <id>-<version>.zip.")
    preview: str | None = Field(default=None, description="Asset name of the card image.")


class PackageRelease(_Model):
    """``packages.json``: the packages one release of a package repository publishes."""

    schema_: Literal["saengine.packageRelease/1"] = Field(
        default="saengine.packageRelease/1", alias="schema"
    )
    repository: str = Field(pattern=REPOSITORY)
    tag: str
    kind: Kind
    packages: list[ReleasePackage]


class IndexPackage(_PackageFacts):
    """One version of a package the app may install."""

    url: str = Field(description="HTTPS URL of the zip (a GitHub release asset).")
    preview: str | None = Field(default=None, description="HTTPS URL of the card image.")
    source: SourceKind
    repository: str = Field(pattern=REPOSITORY)
    tag: str = Field(description="The release that published it.")


class PackageIndex(_Model):
    """``index.json``: every package version the app may install, pinned by size and SHA-256."""

    schema_: Literal["saengine.packageIndex/1"] = Field(
        default="saengine.packageIndex/1", alias="schema"
    )
    generated: str = Field(description="UTC time it was built, ISO 8601.")
    packages: list[IndexPackage]


SCHEMAS: dict[str, type[BaseModel]] = {
    "package-sources.schema.json": Sources,
    "package-release.schema.json": PackageRelease,
    "package-index.schema.json": PackageIndex,
}


def schema_documents() -> dict[str, str]:
    docs = {}
    for name, model in SCHEMAS.items():
        schema = model.model_json_schema(by_alias=True)
        schema["$id"] = model.model_fields["schema_"].default
        docs[name] = json.dumps(schema, indent=2) + "\n"
    return docs


def dump(model: BaseModel) -> str:
    """The document as written: by alias, without nulls, two-space indent."""
    return json.dumps(model.model_dump(by_alias=True, exclude_none=True), indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write or check the JSON Schemas.")
    parser.add_argument("--check", action="store_true", help="fail when out of date")
    args = parser.parse_args(argv)
    stale = []
    for name, text in schema_documents().items():
        path = SCHEMAS_DIR / name
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        if current != text:
            if args.check:
                stale.append(name)
            else:
                SCHEMAS_DIR.mkdir(exist_ok=True)
                path.write_text(text, encoding="utf-8", newline="\n")
    if stale:
        print(f"out of date (run scripts/index_model.py): {', '.join(stale)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
