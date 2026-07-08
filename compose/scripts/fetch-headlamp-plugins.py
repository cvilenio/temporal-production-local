#!/usr/bin/env python3
"""Fetch the pinned Headlamp UI plugins into the bind-mounted plugins dir.

Headlamp runs as a host-plane Compose container (docker-compose.yml, ADR-0014)
and loads UI plugins from `/headlamp/plugins`, one subdir per plugin. We bind-
mount compose/deployment/headlamp/plugins/ there; this script populates it.

Single source of truth is config/dependencies.yaml `headlamp.plugins` — same
"one spec, no drift" pattern as render-deps.py (ADR-0025), so there is no second
version pin and versions-audit needs no row. For each plugin we download the
pinned release tarball, verify its sha256, and extract it into the target dir.

Idempotent + offline-friendly: a plugin already extracted at the pinned version
(recorded in <plugin>/.headlamp-plugin-version) is skipped with NO network call,
so re-running — or `just host-plane-up-cloud` on a machine that's already fetched — is
free and works offline. Bump the version/sha256 in the manifest to force a
re-fetch. Run via `just headlamp-plugins`; stdlib + pyyaml only.
"""

from __future__ import annotations

import hashlib
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "config" / "dependencies.yaml"
PLUGINS_DIR = REPO_ROOT / "compose" / "deployment" / "headlamp" / "plugins"
STAMP = ".headlamp-plugin-version"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_one(name: str, spec: dict) -> bool:
    """Ensure plugin `name` is present at the pinned version. Return True if it
    changed (downloaded), False if the up-to-date copy was reused."""
    version = str(spec["version"])
    want_sha = str(spec["sha256"]).lower()
    url = str(spec["url"])
    dest = PLUGINS_DIR / name
    stamp = dest / STAMP

    if (
        stamp.exists()
        and stamp.read_text().strip() == version
        and (dest / "main.js").exists()
    ):
        print(f"  [skip ] {name} {version} already present")
        return False

    print(f"  [fetch] {name} {version} <- {url}")
    with tempfile.TemporaryDirectory() as tmp:
        tgz = Path(tmp) / "plugin.tar.gz"
        urllib.request.urlretrieve(url, tgz)  # noqa: S310 — pinned https release URL
        got_sha = sha256_file(tgz)
        if got_sha != want_sha:
            sys.exit(
                f"FAIL: {name} sha256 mismatch\n  expected {want_sha}\n  got      {got_sha}\n"
                "Refusing to extract. Re-pin the manifest if this bump is intentional."
            )
        # The tarball's top-level dir is the plugin name (e.g. keda/main.js); extract
        # into PLUGINS_DIR so it lands at PLUGINS_DIR/<name>/. Replace any stale copy.
        if dest.exists():
            _rmtree(dest)
        with tarfile.open(tgz) as tf:
            tf.extractall(PLUGINS_DIR, filter="data")  # filter blocks path traversal
    stamp.write_text(version + "\n")
    print(
        f"  [ok   ] {name} {version} verified + extracted to {dest.relative_to(REPO_ROOT)}"
    )
    return True


def _rmtree(path: Path) -> None:
    for child in path.iterdir():
        if child.is_dir():
            _rmtree(child)
        else:
            child.unlink()
    path.rmdir()


def main() -> None:
    plugins = (yaml.safe_load(MANIFEST.read_text()).get("headlamp") or {}).get(
        "plugins"
    ) or {}
    if not plugins:
        print("No headlamp.plugins pinned in config/dependencies.yaml — nothing to do.")
        return
    PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Headlamp plugins -> {PLUGINS_DIR.relative_to(REPO_ROOT)}/")
    changed = sum(fetch_one(name, spec) for name, spec in sorted(plugins.items()))
    if changed:
        print(
            f"\n{changed} plugin(s) updated. If headlamp is already running, "
            "`just headlamp-reload` to load them."
        )
    else:
        print("\nAll plugins already at pinned versions.")


if __name__ == "__main__":
    main()
