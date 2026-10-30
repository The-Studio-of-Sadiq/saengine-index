"""Check the packages of a package repository, as its CI does on every pull request.

For each package (a top-level folder with ``pack.json``) that the pull request changes, or every
package with ``--all``:

1. SAEngine's own validator: ``saengine codepack validate --kind <kind> <folder> --json`` (the
   manifest, the tables or templates against their models, no programs, sizes, the preview, a
   ``.civil`` template being inputs only). This script adds only what a repository needs on top;
   it never re-implements a check of the validator.
2. The folder is named after the package id.
3. ``licence`` is an SPDX expression of accepted licences; a package with ``hooks`` (code that
   runs inside SAEngine) has an AGPL-3.0-compatible code licence (default AGPL-3.0-only).
4. The preview is small: at most 256 KB, and a PNG at most 1024 × 1024 pixels.
5. A package with ``hooks`` has its own CODEOWNERS entry, so that with "Require review from Code
   Owners" it is not merged without a second reviewer.
6. A changed package has a new version: releases are immutable, and the index pins each
   ``<id>-<version>.zip`` by its hash.
7. A package listed in ``mirrors.json`` (a read-only copy of a package built into SAEngine) is
   identical, file by file, to the SAEngine commit it names.

    python scripts/check_packages.py --repo . --kind designCode --saengine ../SAEngine --all
    python scripts/check_packages.py --repo . --kind designCode --saengine ../SAEngine --base <sha>

``--saengine`` is a checkout of the SAEngine repository (``python/src`` is enough; mirrors are
fetched by commit). Exit code 1 when a package has an error. On GitHub Actions the errors are
also written as annotations.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import struct
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

MANIFEST = "pack.json"
MIRRORS_FILE = "mirrors.json"
MAX_PREVIEW_BYTES = 256 * 1024
MAX_PREVIEW_PIXELS = 1024
# SPDX ids a package may use (data and templates: CC-BY-4.0 by default).
ACCEPTED_LICENCES = frozenset(
    {
        "CC-BY-4.0", "CC-BY-SA-4.0", "CC0-1.0", "ODbL-1.0", "PDDL-1.0",
        "AGPL-3.0-only", "AGPL-3.0-or-later", "GPL-3.0-only", "GPL-3.0-or-later",
        "LGPL-3.0-only", "LGPL-3.0-or-later", "MIT", "Apache-2.0", "BSD-2-Clause",
        "BSD-3-Clause",
    }
)  # fmt: skip
# Licences under which code (hooks) may run inside SAEngine (AGPL-3.0-only): AGPL-3.0 by default.
CODE_LICENCES = frozenset(
    {
        "AGPL-3.0-only", "AGPL-3.0-or-later", "GPL-3.0-only", "GPL-3.0-or-later",
        "LGPL-3.0-only", "LGPL-3.0-or-later", "MIT", "Apache-2.0", "BSD-2-Clause",
        "BSD-3-Clause",
    }
)  # fmt: skip
_SPDX_TOKEN = re.compile(r"\(|\)|\bAND\b|\bOR\b|\bWITH\b|[A-Za-z0-9.+-]+")


@dataclass
class Report:
    package: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)  # message -> file, for annotations


def git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8"
    )
    if check and result.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout


# ---------------------------------------------------------------- which packages


def all_packages(repo: Path) -> list[str]:
    return sorted(
        p.name
        for p in repo.iterdir()
        if p.is_dir() and not p.name.startswith(".") and (p / MANIFEST).is_file()
    )


def changed_packages(repo: Path, base: str) -> tuple[list[str], list[str]]:
    """(the changed packages that exist now, the package folders removed) since ``base``."""
    names = git(repo, "diff", "--name-only", f"{base}...HEAD").splitlines()
    tops = {n.split("/", 1)[0] for n in names if "/" in n}
    if MIRRORS_FILE in names:
        tops |= set(read_mirrors(repo))
    present = sorted(t for t in tops if (repo / t / MANIFEST).is_file())
    removed = sorted(t for t in tops if not (repo / t).exists() and _existed(repo, base, t))
    return present, removed


def _existed(repo: Path, base: str, top: str) -> bool:
    return (
        subprocess.run(
            ["git", "-C", str(repo), "cat-file", "-e", f"{base}:{top}/{MANIFEST}"],
            capture_output=True,
        ).returncode
        == 0
    )


# ---------------------------------------------------------------- the checks


def run_validator(saengine_src: Path, folder: Path, kind: str) -> dict:
    """SAEngine's validator on ``folder`` (its JSON output)."""
    env = {**os.environ, "PYTHONPATH": str(saengine_src)}
    code = "import sys; from saengine.cli.main import main; sys.exit(main())"
    result = subprocess.run(
        [sys.executable, "-c", code, "codepack", "validate", str(folder), "--kind", kind, "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    try:
        return json.loads(result.stdout)
    except ValueError:
        message = f"the validator failed (exit {result.returncode}): {result.stderr[-2000:]}"
        return {"ok": False, "problems": [{"severity": "error", "message": message.strip()}]}


def licence_problems(expression: str | None, hooks: bool) -> list[str]:
    if not expression or not expression.strip():
        return ["licence is missing: give an SPDX id (CC-BY-4.0 for data and templates)"]
    tokens = _SPDX_TOKEN.findall(expression)
    if "".join(tokens).replace(" ", "") != expression.replace(" ", ""):
        return [f"licence '{expression}' is not an SPDX expression"]
    ids = [t for t in tokens if t not in ("(", ")", "AND", "OR", "WITH")]
    if "WITH" in tokens:
        return [f"licence '{expression}': exceptions (WITH) are not accepted"]
    problems = [
        f"licence '{i}' is not one of the accepted SPDX ids: {', '.join(sorted(ACCEPTED_LICENCES))}"
        for i in ids
        if i not in ACCEPTED_LICENCES
    ]
    if hooks and not any(i in CODE_LICENCES for i in ids):
        problems.append(
            f"licence '{expression}': a package with hooks needs a licence for its code that "
            "SAEngine (AGPL-3.0-only) can run, e.g. 'CC-BY-4.0 AND AGPL-3.0-only'"
        )
    return problems


def png_size(path: Path) -> tuple[int, int] | None:
    head = path.read_bytes()[:24]
    if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", head[16:24])


def preview_problems(folder: Path, preview: str | None) -> list[str]:
    if not preview:
        return []
    path = folder / preview
    if not path.is_file():
        return []  # the validator reports it
    problems = []
    size = path.stat().st_size
    if size > MAX_PREVIEW_BYTES:
        problems.append(
            f"the preview is {size // 1024} KB: at most {MAX_PREVIEW_BYTES // 1024} KB in a "
            "package repository"
        )
    dims = png_size(path) if path.suffix.lower() == ".png" else None
    if dims and max(dims) > MAX_PREVIEW_PIXELS:
        problems.append(
            f"the preview is {dims[0]} × {dims[1]} px: at most {MAX_PREVIEW_PIXELS} px a side"
        )
    return problems


def codeowners_rules(repo: Path) -> list[tuple[str, list[str]]]:
    for rel in (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS"):
        path = repo / rel
        if path.is_file():
            rules = []
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.split("#", 1)[0].strip()
                if line:
                    pattern, *owners = line.split()
                    rules.append((pattern, owners))
            return rules
    return []


def _codeowners_match(pattern: str, path: str) -> bool:
    """A CODEOWNERS (gitignore-style) pattern against a repository path (no leading '/')."""
    anchored = pattern.startswith("/")
    p = pattern.strip("/")
    if pattern.endswith("/"):
        return path.startswith(p + "/") if anchored else f"/{p}/" in f"/{path}"
    if anchored or "/" in p:
        return fnmatch.fnmatchcase(path, p) or path.startswith(p + "/")
    return any(fnmatch.fnmatchcase(part, p) for part in path.split("/"))


def owners_of(rules: list[tuple[str, list[str]]], path: str) -> tuple[str | None, list[str]]:
    """The last matching rule (as GitHub reads CODEOWNERS): its pattern and owners."""
    found: tuple[str | None, list[str]] = (None, [])
    for pattern, owners in rules:
        if _codeowners_match(pattern, path):
            found = (pattern, owners)
    return found


def hooks_owner_problems(repo: Path, pack_id: str, hooks: str) -> list[str]:
    pattern, owners = owners_of(codeowners_rules(repo), f"{pack_id}/{hooks}")
    if pattern is None or pattern in ("*", "/*", "**", "/**") or not owners:
        return [
            f"the package has hooks ({hooks}): add a CODEOWNERS entry for /{pack_id}/ naming "
            "its reviewers, so it is not merged without a second review"
        ]
    return []


def version_problems(repo: Path, base: str | None, pack_id: str, version: str) -> list[str]:
    """A package changed since ``base`` without a new version."""
    if base is None or not _existed(repo, base, pack_id):
        return []
    if not git(repo, "diff", "--name-only", f"{base}...HEAD", "--", pack_id).strip():
        return []
    try:
        old = json.loads(git(repo, "show", f"{base}:{pack_id}/{MANIFEST}")).get("version", "1.0.0")
    except ValueError:
        return []
    if old == version:
        return [
            f"the package changed but its version is still {version}: raise 'version' in "
            f"{MANIFEST} (published versions never change)"
        ]
    return []


def read_mirrors(repo: Path) -> dict[str, dict]:
    path = repo / MIRRORS_FILE
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("packages", {})


def mirror_problems(repo: Path, saengine: Path, pack_id: str, entry: dict) -> list[str]:
    """Files of the mirror that differ from the SAEngine commit it names."""
    commit, path = entry.get("commit", ""), entry.get("path", "").strip("/")
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or not path:
        return [f"{MIRRORS_FILE}: '{pack_id}' needs a full commit and a path"]
    if subprocess.run(
        ["git", "-C", str(saengine), "cat-file", "-e", f"{commit}^{{tree}}"], capture_output=True
    ).returncode:
        fetch = subprocess.run(
            ["git", "-C", str(saengine), "fetch", "-q", "--depth=1", "--filter=blob:none",
             "origin", commit],
            capture_output=True, text=True,
        )  # fmt: skip
        if fetch.returncode:
            return [
                f"{MIRRORS_FILE}: cannot fetch SAEngine commit {commit}: {fetch.stderr.strip()}"
            ]
    listing = git(saengine, "ls-tree", "-r", commit, "--", path + "/")
    expected = {}
    for line in listing.splitlines():
        meta, name = line.split("\t", 1)
        expected[name[len(path) + 1 :]] = meta.split()[2]
    if not expected:
        return [f"{MIRRORS_FILE}: SAEngine {commit[:12]} has no folder {path}"]
    folder = repo / pack_id
    actual = {
        p.relative_to(folder).as_posix(): p
        for p in folder.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }
    problems = []
    for rel in sorted(set(expected) | set(actual)):
        if rel not in actual:
            problems.append(f"mirror: {rel} is missing (SAEngine {commit[:12]} has it)")
        elif rel not in expected:
            problems.append(f"mirror: {rel} is not in SAEngine {commit[:12]}")
        elif (
            git(repo, "hash-object", f"--path={pack_id}/{rel}", str(actual[rel])).strip()
            != expected[rel]
        ):
            problems.append(f"mirror: {rel} differs from SAEngine {commit[:12]}")
    if problems:
        problems.append(
            "a mirrored package is edited in SAEngine and copied here with "
            "scripts/marketplace/mirror_packages.py, never edited here"
        )
    return problems


def check_package(repo: Path, pack_id: str, kind: str, saengine: Path, base: str | None) -> Report:
    report = Report(pack_id)
    folder = repo / pack_id
    result = run_validator(saengine / "python" / "src", folder, kind)
    for p in result.get("problems", []):
        text = p["message"] + (f" ({p['file']})" if p.get("file") else "")
        (report.errors if p.get("severity", "error") == "error" else report.warnings).append(text)
        if p.get("file"):
            report.files[text] = f"{pack_id}/{p['file']}"
    if not result.get("ok", False) and not report.errors:
        report.errors.append("the validator found errors")
    try:
        manifest = json.loads((folder / MANIFEST).read_text(encoding="utf-8-sig"))
    except ValueError as e:
        report.errors.append(f"{MANIFEST} cannot be read: {e}")
        return report
    if not isinstance(manifest, dict):
        report.errors.append(f"{MANIFEST} is not an object")
        return report
    if manifest.get("id") != pack_id:
        report.errors.append(f"the folder is '{pack_id}' but the id is '{manifest.get('id')}'")
    hooks = manifest.get("hooks")
    report.errors += licence_problems(manifest.get("licence"), bool(hooks))
    report.errors += preview_problems(folder, manifest.get("preview"))
    if hooks:
        report.errors += hooks_owner_problems(repo, pack_id, hooks)
    report.errors += version_problems(repo, base, pack_id, str(manifest.get("version", "1.0.0")))
    mirror = read_mirrors(repo).get(pack_id)
    if mirror is not None:
        report.errors += mirror_problems(repo, saengine, pack_id, mirror)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", type=Path, default=Path("."), help="the package repository")
    parser.add_argument(
        "--kind", required=True, choices=("designCode", "structureTemplate", "buildingTemplate")
    )
    parser.add_argument("--saengine", type=Path, required=True, help="SAEngine checkout")
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--all", action="store_true", help="every package")
    which.add_argument("--base", help="the packages changed since this commit")
    args = parser.parse_args(argv)
    repo = args.repo.resolve()
    if args.all:
        packages, removed = all_packages(repo), []
    else:
        packages, removed = changed_packages(repo, args.base)
    annotate = os.environ.get("GITHUB_ACTIONS") == "true"
    failed = False
    for pack_id in removed:
        print(f"{pack_id}: removed (its published releases stay in the index's history)")
    if not packages:
        print("no package changed")
    for pack_id in packages:
        report = check_package(repo, pack_id, args.kind, args.saengine.resolve(), args.base)
        status = "FAILED" if report.errors else "ok"
        print(f"{pack_id}: {status} ({len(report.errors)} errors, {len(report.warnings)} warnings)")
        for kind, items in (("error", report.errors), ("warning", report.warnings)):
            for message in items:
                print(f"  {kind}: {message}")
                if annotate:
                    file = report.files.get(message, f"{pack_id}/{MANIFEST}")
                    print(f"::{kind} file={file}::{pack_id}: {message}")
        failed |= bool(report.errors)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
