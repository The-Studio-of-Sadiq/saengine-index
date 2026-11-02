"""Build the static store site of a marketplace package repository (GitHub Pages).

Each package repository (``saengine-design-codes``, ``saengine-structure-templates``,
``saengine-building-templates``) and ``saengine-index`` gets its own small store site: a page per
package with its name, kind, version, author, licence, description, requirements, preview and
the SHA-256 of the zip, and an index page that lists them (docs/marketplace-plan.md, round M3).
The site is generated from the repository itself; the workflow ``store-pages.yml`` beside this
script runs it and publishes the result on an orphan ``gh-pages`` branch.

The site is a catalogue only: installing happens in SAEngine (Packages > Browse), which checks
the signed index and every download's SHA-256. The site never offers a download that the app
would not verify, so it links to the release asset and prints its SHA-256.

Usage (standard library only; run with ``python -I``)::

    python -I build_store.py --repo . --out _site --title "SAEngine design codes"
    python -I build_store.py --index index.json --out _site --title "SAEngine packages"

``--repo``: a package repository checkout (``<id>/pack.json`` folders); ``--index``: an
``index.json`` (``saengine.packageIndex/1``), whose entries carry the release URL and SHA-256.
With both, the index adds the download links to the repository's packages.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
from pathlib import Path

KIND_NAMES = {
    "designCode": "Design code",
    "structureTemplate": "Structure template",
    "buildingTemplate": "Building template",
    "languagePack": "Language pack",
}
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
PREVIEW_TYPES = {".png", ".jpg", ".jpeg", ".webp"}
MAX_PREVIEW_BYTES = 256 * 1024

STYLE = """\
:root { color-scheme: light dark; --fg: #1f2328; --muted: #59636e; --bg: #ffffff;
  --card: #f6f8fa; --line: #d1d9e0; --link: #0969da; }
@media (prefers-color-scheme: dark) { :root { --fg: #e6edf3; --muted: #9198a1; --bg: #0d1117;
  --card: #151b23; --line: #3d444d; --link: #4493f8; } }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 960px; margin: 0 auto; padding: 24px 16px 48px; }
a { color: var(--link); }
h1 { font-size: 26px; margin: 0 0 4px; } h2 { font-size: 18px; margin: 24px 0 8px; }
.muted { color: var(--muted); }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px; }
.card { display: block; padding: 12px; border: 1px solid var(--line); border-radius: 6px;
  background: var(--card); color: inherit; text-decoration: none; }
.card:hover { border-color: var(--link); }
.card img { width: 100%; height: 140px; object-fit: contain; margin-bottom: 8px; }
.badge { display: inline-block; padding: 0 6px; border: 1px solid var(--line); border-radius: 10px;
  font-size: 12px; margin-right: 4px; }
table { border-collapse: collapse; width: 100%; } th, td { text-align: left; padding: 4px 8px;
  border-bottom: 1px solid var(--line); vertical-align: top; } th { width: 30%; }
code { font-size: 12px; word-break: break-all; }
.note { padding: 8px 12px; border-left: 3px solid var(--link); background: var(--card); }
"""


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


def read_repository(repo: Path) -> list[dict]:
    """Every ``<id>/pack.json`` of a package repository, with its folder."""
    packages = []
    for manifest in sorted(repo.glob("*/pack.json")):
        try:
            doc = json.loads(manifest.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as error:
            print(f"skipped {manifest}: {error}", file=sys.stderr)
            continue
        if not isinstance(doc, dict) or not ID_PATTERN.match(str(doc.get("id", ""))):
            print(f"skipped {manifest}: no valid id", file=sys.stderr)
            continue
        doc.setdefault("kind", "designCode")
        doc["_folder"] = manifest.parent
        packages.append(doc)
    return packages


def read_index(path: Path) -> list[dict]:
    """The newest entry of each package in an ``index.json``."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("schema") != "saengine.packageIndex/1":
        raise SystemExit(f"{path} is not a saengine.packageIndex/1 document")
    newest: dict[str, dict] = {}

    def key(version: str) -> tuple:
        return tuple(int(p) if p.isdigit() else 0 for p in re.split(r"[.\-+]", version))

    for entry in doc.get("packages", []):
        pid = str(entry.get("id", ""))
        if not ID_PATTERN.match(pid):
            continue
        if pid not in newest or key(str(entry.get("version", ""))) > key(
            str(newest[pid].get("version", ""))
        ):
            newest[pid] = entry
    return list(newest.values())


def merge(repo_packages: list[dict], index_packages: list[dict]) -> list[dict]:
    """The repository's packages with the index's release URL, size and SHA-256 when the index
    has the same id and version; index entries without a folder are added as they are."""
    by_id = {p["id"]: p for p in index_packages}
    merged = []
    for pkg in repo_packages:
        entry = by_id.pop(pkg["id"], None)
        if entry and str(entry.get("version")) == str(pkg.get("version")):
            pkg = {**pkg, **{k: entry[k] for k in ("url", "size", "sha256") if k in entry}}
        merged.append(pkg)
    return merged + list(by_id.values())


def _preview(pkg: dict, out: Path) -> str | None:
    """Copies a local preview (small images only) into the site: its site path."""
    folder = pkg.get("_folder")
    name = pkg.get("preview")
    if not folder or not isinstance(name, str):
        return None
    source = (folder / name).resolve()
    if (
        not source.is_file()
        or not source.is_relative_to(folder.resolve())
        or source.suffix.lower() not in PREVIEW_TYPES
        or source.stat().st_size > MAX_PREVIEW_BYTES
    ):
        return None
    target = out / "previews" / f"{pkg['id']}{source.suffix.lower()}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return f"previews/{target.name}"


def _page(title: str, body: str, site_title: str) -> str:
    return (
        "<!doctype html>\n<html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{_esc(title)}</title><link rel='stylesheet' href='style.css'></head>"
        f"<body><main><p class='muted'><a href='index.html'>{_esc(site_title)}</a></p>"
        f"{body}</main></body></html>\n"
    )


def _requires(pkg: dict) -> str:
    requires = pkg.get("requires") or {}
    parts = []
    if requires.get("saengine"):
        parts.append(f"SAEngine {requires['saengine']}")
    for other, version in (requires.get("packages") or {}).items():
        parts.append(f"{other} {version}".strip())
    return ", ".join(parts)


def package_page(pkg: dict, preview: str | None, site_title: str) -> str:
    rows = [
        ("Id", pkg["id"]),
        ("Kind", KIND_NAMES.get(pkg.get("kind"), pkg.get("kind"))),
        ("Version", pkg.get("version")),
        ("Country / region", " · ".join(str(v) for v in (pkg.get("country"), pkg.get("region")) if v)),
        ("Extends", pkg.get("extends")),
        ("Needs", _requires(pkg)),
        ("Author", pkg.get("author")),
        ("Licence", pkg.get("licence") or pkg.get("license")),
        ("Contains code", "yes (Python hooks: they run only after you trust them in SAEngine)"
         if pkg.get("hooks") else "no (data only)"),
        ("Download size", f"{pkg['size']:,} bytes" if isinstance(pkg.get("size"), int) else None),
        ("SHA-256", f"<code>{_esc(pkg['sha256'])}</code>" if pkg.get("sha256") else None),
    ]  # fmt: skip
    table = "".join(
        f"<tr><th>{_esc(k)}</th><td>{v if k == 'SHA-256' else _esc(v)}</td></tr>"
        for k, v in rows
        if v
    )
    image = f"<img src='{_esc(preview)}' alt='' style='max-width:100%'>" if preview else ""
    download = (
        f"<p><a href='{_esc(pkg['url'])}'>Download the zip</a> (for Install from File... "
        "offline; check its SHA-256 above)</p>"
        if str(pkg.get("url", "")).startswith("https://")
        else ""
    )
    body = (
        f"<h1>{_esc(pkg.get('name') or pkg['id'])}</h1>"
        f"<p class='muted'>{_esc(pkg.get('description') or '')}</p>{image}"
        f"<table>{table}</table>"
        "<p class='note'>Install it in SAEngine: ≡ › Packages... › Browse. SAEngine checks the "
        "signed package list and the SHA-256 of every download before it installs.</p>"
        f"{download}"
    )
    return _page(f"{pkg.get('name') or pkg['id']} · {site_title}", body, site_title)


def index_page(packages: list[dict], previews: dict[str, str | None], site_title: str,
               intro: str) -> str:  # fmt: skip
    sections = []
    for kind, label in KIND_NAMES.items():
        items = sorted((p for p in packages if p.get("kind", "designCode") == kind),
                       key=lambda p: str(p.get("name") or p["id"]).lower())  # fmt: skip
        if not items:
            continue
        cards = []
        for pkg in items:
            image = (
                f"<img src='{_esc(previews.get(pkg['id']))}' alt=''>"
                if previews.get(pkg["id"])
                else ""
            )
            badges = "".join(
                f"<span class='badge'>{_esc(b)}</span>"
                for b in (
                    f"v{pkg.get('version')}",
                    pkg.get("country"),
                    pkg.get("licence"),
                    "contains code" if pkg.get("hooks") else None,
                )
                if b
            )
            cards.append(
                f"<a class='card' href='{_esc(pkg['id'])}.html'>{image}"
                f"<strong>{_esc(pkg.get('name') or pkg['id'])}</strong><br>{badges}"
                f"<div class='muted'>{_esc(pkg.get('description') or '')}</div></a>"
            )
        sections.append(f"<h2>{_esc(label)}s</h2><div class='grid'>{''.join(cards)}</div>")
    body = f"<h1>{_esc(site_title)}</h1><p class='muted'>{_esc(intro)}</p>" + (
        "".join(sections) or "<p>No packages are published yet.</p>"
    )
    return _page(site_title, body, site_title)


def build(packages: list[dict], out: Path, site_title: str, intro: str) -> list[Path]:
    """Writes the site into ``out`` (emptied first): the files written."""
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / ".nojekyll").write_text("", encoding="ascii")
    (out / "style.css").write_text(STYLE, encoding="utf-8")
    previews = {p["id"]: _preview(p, out) for p in packages}
    written = [out / "index.html"]
    (out / "index.html").write_text(index_page(packages, previews, site_title, intro),
                                    encoding="utf-8")  # fmt: skip
    for pkg in packages:
        path = out / f"{pkg['id']}.html"
        path.write_text(package_page(pkg, previews[pkg["id"]], site_title), encoding="utf-8")
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", type=Path, help="a package repository checkout")
    parser.add_argument("--index", type=Path, help="an index.json")
    parser.add_argument("--out", type=Path, default=Path("_site"))
    parser.add_argument("--title", default="SAEngine packages")
    parser.add_argument("--intro", default="Packages for SAEngine, the open-source structural "
                        "engineering IDE. Install them from SAEngine's Packages page.")  # fmt: skip
    args = parser.parse_args(argv)
    if not args.repo and not args.index:
        parser.error("give --repo, --index or both")
    packages = read_repository(args.repo) if args.repo else []
    if args.index:
        packages = merge(packages, read_index(args.index))
    written = build(packages, args.out, args.title, args.intro)
    print(f"{args.out}: {len(written)} pages, {len(packages)} packages")
    return 0


if __name__ == "__main__":
    sys.exit(main())
