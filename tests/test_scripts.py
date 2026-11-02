"""Tests of the index scripts. The end-to-end check of a package repository needs SAEngine's
validator: set SAENGINE_CHECKOUT to a SAEngine checkout (python/src is enough), else it is
skipped."""

from __future__ import annotations

import base64
import json
import os
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import build_index  # noqa: E402
import check_packages  # noqa: E402
import index_model  # noqa: E402
import package_release  # noqa: E402
import sign_index  # noqa: E402

REPO = "The-Studio-of-Sadiq/saengine-structure-templates"
PRESET = {
    "name": "Warren truss 24 m",
    "shape": {"kind": "planeTruss", "span": 24000, "depth": 2400, "panels": 8,
              "pattern": "warren", "memberType": "MEMBER"},
}  # fmt: skip


def png(width: int, height: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data)
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    raw = b"".join(b"\0" + b"\xff" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def write_package(repo: Path, pack_id: str = "trusses", version: str = "1.0.0", **extra) -> Path:
    folder = repo / pack_id
    (folder / "templates").mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": "saengine.codePack/1",
        "kind": "structureTemplate",
        "id": pack_id,
        "name": pack_id.title(),
        "version": version,
        "licence": "CC-BY-4.0",
        "preview": "preview.png",
        **extra,
    }
    (folder / "pack.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (folder / "templates" / "warren.json").write_text(json.dumps(PRESET), encoding="utf-8")
    (folder / "preview.png").write_bytes(png(8, 4))
    return folder


def git(repo: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env
    ).stdout.strip()


def commit_all(repo: Path, message: str = "c") -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


# ---------------------------------------------------------------- repository checks


@pytest.mark.parametrize(
    ("expression", "hooks", "ok"),
    [
        ("CC-BY-4.0", False, True),
        ("CC-BY-4.0 AND AGPL-3.0-only", True, True),
        ("(CC-BY-4.0 OR CC0-1.0)", False, True),
        ("CC-BY-4.0", True, False),  # code needs a code licence
        ("Proprietary", False, False),
        ("GPL-2.0-only WITH Classpath-exception-2.0", False, False),
        ("", False, False),
        (None, False, False),
    ],
)
def test_licences(expression, hooks, ok):
    assert (check_packages.licence_problems(expression, hooks) == []) is ok


def test_preview_size_limits(tmp_path):
    (tmp_path / "small.png").write_bytes(png(8, 4))
    (tmp_path / "wide.png").write_bytes(png(2000, 1))
    (tmp_path / "big.webp").write_bytes(b"\0" * (300 * 1024))
    assert check_packages.png_size(tmp_path / "small.png") == (8, 4)
    assert check_packages.preview_problems(tmp_path, "small.png") == []
    assert "2000 × 1 px" in check_packages.preview_problems(tmp_path, "wide.png")[0]
    assert "300 KB" in check_packages.preview_problems(tmp_path, "big.webp")[0]


def test_hooks_need_their_own_codeowners(tmp_path):
    (tmp_path / ".github").mkdir()
    owners = tmp_path / ".github" / "CODEOWNERS"
    owners.write_text("* @owner\n", encoding="utf-8")
    assert check_packages.hooks_owner_problems(tmp_path, "coded", "hooks.py")
    owners.write_text("* @owner\n/coded/ @owner @reviewer  # code\n", encoding="utf-8")
    assert check_packages.hooks_owner_problems(tmp_path, "coded", "hooks.py") == []
    assert check_packages.hooks_owner_problems(tmp_path, "other", "hooks.py")


def test_a_changed_package_needs_a_new_version(tmp_path):
    git(tmp_path, "init", "-q")
    write_package(tmp_path)
    base = commit_all(tmp_path)
    (tmp_path / "trusses" / "templates" / "pratt.json").write_text(
        json.dumps({**PRESET, "name": "Pratt"}), encoding="utf-8"
    )
    commit_all(tmp_path)
    assert check_packages.changed_packages(tmp_path, base) == (["trusses"], [])
    assert "still 1.0.0" in check_packages.version_problems(tmp_path, base, "trusses", "1.0.0")[0]
    assert check_packages.version_problems(tmp_path, base, "trusses", "1.1.0") == []
    assert check_packages.version_problems(tmp_path, None, "trusses", "1.0.0") == []


def test_mirror_must_equal_its_commit(tmp_path):
    source = tmp_path / "saengine"
    pack = source / "python/src/saengine/codes/packs/trusses"
    git(source.parent, "init", "-q", str(source))
    write_package(pack.parent)
    commit = commit_all(source)
    repo = tmp_path / "repo"
    git(tmp_path, "init", "-q", str(repo))
    write_package(repo)
    entry = {"repository": "x/y", "path": "python/src/saengine/codes/packs/trusses",
             "commit": commit}  # fmt: skip
    assert check_packages.mirror_problems(repo, source, "trusses", entry) == []
    (repo / "trusses" / "notes.txt").write_text("edited here", encoding="utf-8")
    (repo / "trusses" / "templates" / "warren.json").write_text("{}", encoding="utf-8")
    problems = check_packages.mirror_problems(repo, source, "trusses", entry)
    assert any("notes.txt is not in SAEngine" in p for p in problems)
    assert any("warren.json differs" in p for p in problems)
    assert "mirror_packages.py" in problems[-1]
    assert check_packages.mirror_problems(repo, source, "trusses", {**entry, "commit": "abc"})


# ---------------------------------------------------------------- release and index


def test_release_zips_are_reproducible_and_versions_immutable(tmp_path):
    repo = tmp_path / "repo"
    write_package(repo)
    (repo / "trusses" / "__pycache__").mkdir()
    (repo / "trusses" / "__pycache__" / "x.pyc").write_bytes(b"\0")
    first = package_release.build(repo, "structureTemplate", REPO, "packages-1", tmp_path / "a")
    second = package_release.build(repo, "structureTemplate", REPO, "packages-1", tmp_path / "b")
    p = first.packages[0]
    assert p.sha256 == second.packages[0].sha256 and p.file == "trusses-1.0.0.zip"
    assert p.preview == "trusses-1.0.0.preview.png" and not p.hooks
    import zipfile

    with zipfile.ZipFile(tmp_path / "a" / p.file) as zf:
        assert zf.namelist() == [
            "trusses/pack.json",
            "trusses/preview.png",
            "trusses/templates/warren.json",
        ]
    # Already published with the same content: nothing new.
    again = package_release.build(
        repo, "structureTemplate", REPO, "packages-2", tmp_path / "c", None,
        [tmp_path / "a" / "packages.json"],
    )  # fmt: skip
    assert again.packages == []
    # Changed without a new version: refused.
    (repo / "trusses" / "templates" / "warren.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit, match="raise its version"):
        package_release.build(
            repo, "structureTemplate", REPO, "packages-2", tmp_path / "d", None,
            [tmp_path / "a" / "packages.json"],
        )  # fmt: skip


def _sources(*pairs: tuple[str, str]) -> index_model.Sources:
    return index_model.Sources(sources=[index_model.Source(repository=r, kind=k) for r, k in pairs])


def test_index_from_a_local_release(tmp_path):
    repo = tmp_path / "repo"
    write_package(repo)
    out = tmp_path / "release"
    package_release.build(repo, "structureTemplate", REPO, "packages-2026-11-15", out)
    index = build_index.build(
        _sources((REPO, "structureTemplate")), {REPO: out}, generated="2026-11-15T10:00:00Z"
    )
    [entry] = index.packages
    assert entry.url == (
        f"https://github.com/{REPO}/releases/download/packages-2026-11-15/trusses-1.0.0.zip"
    )
    assert entry.preview.endswith("/trusses-1.0.0.preview.png")
    assert entry.source == "official" and entry.tag == "packages-2026-11-15"
    doc = json.loads(index_model.dump(index))
    assert doc["schema"] == "saengine.packageIndex/1" and "hooksSha256" not in doc["packages"][0]

    # A zip that does not match its manifest is refused.
    (out / "trusses-1.0.0.zip").write_bytes(b"tampered")
    with pytest.raises(build_index.BuildError, match="bytes"):
        build_index.build(_sources((REPO, "structureTemplate")), {REPO: out})


def test_index_refuses_wrong_kinds_and_shared_ids(tmp_path):
    repo = tmp_path / "repo"
    write_package(repo)
    out = tmp_path / "release"
    package_release.build(repo, "structureTemplate", REPO, "packages-1", out)
    with pytest.raises(build_index.BuildError, match="from a designCode source"):
        build_index.build(_sources((REPO, "designCode")), {REPO: out})
    other = "The-Studio-of-Sadiq/other"
    out2 = tmp_path / "release2"
    package_release.build(repo, "structureTemplate", other, "packages-1", out2)
    with pytest.raises(build_index.BuildError, match="already published by"):
        build_index.build(
            _sources((REPO, "structureTemplate"), (other, "structureTemplate")),
            {REPO: out, other: out2},
        )


def test_only_https_on_github_is_fetched():
    with pytest.raises(build_index.BuildError, match="not HTTPS on GitHub"):
        build_index._get("http://github.com/x")
    with pytest.raises(build_index.BuildError, match="not HTTPS on GitHub"):
        build_index._get("https://example.com/x.zip")


# ---------------------------------------------------------------- signing


def test_sign_and_verify_with_the_test_key(tmp_path):
    index = tmp_path / "index.json"
    index.write_text('{"schema": "saengine.packageIndex/1"}\n', encoding="utf-8")
    assert sign_index.main(["sign", str(index), "--test-key"]) == 0
    public = sign_index.load_public(
        Path(__file__).resolve().parents[1] / sign_index.TEST_PUBLIC_KEY
    )
    sig = Path(f"{index}.sig")
    assert sign_index.verify(index, sig, public) is None
    assert json.loads(sig.read_text())["keyId"] == sign_index.key_id(public)
    index.write_text('{"schema": "saengine.packageIndex/1", "x": 1}\n', encoding="utf-8")
    assert "does not match" in sign_index.verify(index, sig, public)


def test_wrap_a_raw_signature(tmp_path):
    """The OpenSSL path: a raw signature wrapped and checked against the public key."""
    index = tmp_path / "index.json"
    index.write_bytes(b"{}\n")
    key = sign_index.test_private_key()
    raw = tmp_path / "raw.sig"
    raw.write_bytes(key.sign(index.read_bytes()))
    pem = tmp_path / "public.pem"
    pem.write_text(sign_index.public_pem(key.public_key()), encoding="ascii")
    assert sign_index.main(["wrap", str(index), "--raw", str(raw), "--public", str(pem)]) == 0
    raw.write_bytes(base64.b64decode(base64.b64encode(b"\0" * 64)))
    assert sign_index.main(["wrap", str(index), "--raw", str(raw), "--public", str(pem)]) == 1


def test_schemas_are_up_to_date():
    assert index_model.main(["--check"]) == 0


# ---------------------------------------------------------------- end to end


@pytest.mark.skipif(not os.environ.get("SAENGINE_CHECKOUT"), reason="needs SAENGINE_CHECKOUT")
def test_check_packages_with_the_saengine_validator(tmp_path, capsys):
    saengine = Path(os.environ["SAENGINE_CHECKOUT"]).resolve()
    repo = tmp_path / "repo"
    git(tmp_path, "init", "-q", str(repo))
    write_package(repo)
    base = commit_all(repo)
    args = ["--repo", str(repo), "--kind", "structureTemplate", "--saengine", str(saengine)]
    assert check_packages.main([*args, "--all"]) == 0
    write_package(repo, "broken")
    (repo / "broken" / "templates" / "warren.json").write_text(
        json.dumps({"name": "x", "shape": {"kind": "planeTruss", "span": -1}}), encoding="utf-8"
    )
    (repo / "broken" / "run.bat").write_text("echo", encoding="utf-8")
    commit_all(repo)
    assert check_packages.main([*args, "--base", base]) == 1
    out = capsys.readouterr().out
    assert "broken: FAILED" in out and "run.bat" in out and "trusses" not in out.split("broken")[1]
    assert check_packages.main([*args[:3], "designCode", *args[4:], "--all"]) == 1
