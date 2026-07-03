#!/usr/bin/env python3
"""Fetch pinned Grafana plugins into the bind-mounted plugins dir.

lgtm (grafana/otel-lgtm) runs `bin/grafana server` directly (run-grafana.sh) — it
never runs the classic grafana-cli entrypoint the official grafana/grafana image
uses for GF_INSTALL_PLUGINS, so that env var is a silent no-op here. Its own
GF_PLUGINS_PREINSTALL mechanism reaches grafana.com at boot, which is exactly the
air-gap hang GF_PLUGINS_PREINSTALL_DISABLED=true (docker-compose.yml) exists to
avoid. So plugins are fetched ahead of time instead, same pattern as
fetch-headlamp-plugins.py: download the pinned release zip, verify its sha256,
extract into compose/deployment/grafana/plugins/<id>/, which the lgtm container
bind-mounts at GF_PATHS_PLUGINS (/data/grafana/plugins) — a directory scan at
boot, no network required.

Single source of truth is config/dependencies.yaml `grafana.plugins` — same
"one spec, no drift" pattern as render-deps.py (ADR-0025), so there is no second
version pin and versions-audit needs no row. For each plugin we download the
pinned release zip, verify its sha256, and extract it into the target dir.

Idempotent + offline-friendly: a plugin already extracted at the pinned version
(recorded in <plugin>/.grafana-plugin-version) is skipped with NO network call,
so re-running — or `just up` / `just up-cloud-kind` on a machine that's already
fetched — is free and works offline. Bump the version/sha256 in the manifest to
force a re-fetch. Run via `just grafana-plugins`; stdlib + pyyaml only.
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "config" / "dependencies.yaml"
PLUGINS_DIR = REPO_ROOT / "compose" / "deployment" / "grafana" / "plugins"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    """Extract, rejecting any entry that would escape `dest` (path traversal)."""
    dest = dest.resolve()
    for member in zf.infolist():
        target = (dest / member.filename).resolve()
        if not str(target).startswith(str(dest)):
            sys.exit(
                f"FAIL: zip entry {member.filename!r} escapes extraction dir — refusing to extract."
            )
    zf.extractall(dest)
    # Unlike tarfile, zipfile.extractall does NOT restore Unix permissions — the
    # backend plugin binaries (gpx_clickhouse_*) need their executable bit back
    # or Grafana's plugin loader fails with "permission denied" on exec.
    for member in zf.infolist():
        mode = member.external_attr >> 16
        if mode:
            (dest / member.filename).chmod(mode)


def fetch_one(name: str, spec: dict) -> bool:
    """Ensure plugin `name` is present at the pinned version. Return True if it
    changed (downloaded), False if the up-to-date copy was reused."""
    version = str(spec["version"])
    want_sha = str(spec["sha256"]).lower()
    url = str(spec["url"])
    dest = PLUGINS_DIR / name
    # The stamp file must live OUTSIDE the plugin dir, as a sibling — Grafana's
    # signature check hashes every file under the plugin dir against its signed
    # MANIFEST.txt, so any extra file in there (even our own version marker)
    # makes the whole plugin look "modified" and Grafana refuses to load it.
    stamp = PLUGINS_DIR / f".{name}.version"

    if (
        stamp.exists()
        and stamp.read_text().strip() == version
        and any(dest.rglob("plugin.json"))
    ):
        print(f"  [skip ] {name} {version} already present")
        return False

    print(f"  [fetch] {name} {version} <- {url}")
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / "plugin.zip"
        urllib.request.urlretrieve(url, zip_path)  # noqa: S310 — pinned https release URL
        got_sha = sha256_file(zip_path)
        if got_sha != want_sha:
            sys.exit(
                f"FAIL: {name} sha256 mismatch\n  expected {want_sha}\n  got      {got_sha}\n"
                "Refusing to extract. Re-pin the manifest if this bump is intentional."
            )
        # The zip's top-level dir is the plugin id (e.g. grafana-clickhouse-datasource/
        # plugin.json); extract into PLUGINS_DIR so it lands at PLUGINS_DIR/<id>/.
        # Replace any stale copy first.
        if dest.exists():
            _rmtree(dest)
        with zipfile.ZipFile(zip_path) as zf:
            safe_extract(zf, PLUGINS_DIR)
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
    plugins = (yaml.safe_load(MANIFEST.read_text()).get("grafana") or {}).get(
        "plugins"
    ) or {}
    if not plugins:
        print("No grafana.plugins pinned in config/dependencies.yaml — nothing to do.")
        return
    PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Grafana plugins -> {PLUGINS_DIR.relative_to(REPO_ROOT)}/")
    changed = sum(fetch_one(name, spec) for name, spec in sorted(plugins.items()))
    if changed:
        print(
            f"\n{changed} plugin(s) updated. Restart lgtm to load them: `docker restart lgtm`."
        )
    else:
        print("\nAll plugins already at pinned versions.")


if __name__ == "__main__":
    main()
