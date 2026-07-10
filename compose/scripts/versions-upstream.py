#!/usr/bin/env python3
"""Report Tier-1 (Temporal) pinned-vs-latest-stable from upstream registries.

The repo wants to track the latest stable release of everything Temporal (ADR-0025).
config/dependencies.yaml records what we pin; this tool fetches what upstream ships
and shows the gap, so the architect can decide when to bump.

NETWORK tool (Resolve tier, ADR-0013): queries PyPI, Docker Hub, GitHub releases, and
the Terraform Registry. Deliberately NOT wired into any gate — upstream cutting a
release must never turn the offline lint red. Run ad hoc via `just versions-upstream`.

  exit 0 always, UNLESS `--strict` is passed and something is BEHIND (then exit 1).

stdlib only (urllib + json). Honors GITHUB_TOKEN to lift the 60/hr anonymous rate
limit. Unreachable sources degrade to an ERROR row, never a crash.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "config" / "dependencies.yaml"
TIMEOUT = 10


# ── version helpers ────────────────────────────────────────────────────────--


def vtuple(s: str) -> tuple[int, ...]:
    """Leading numeric dotted version → tuple of ints (strips a leading 'v' and any
    pre-release/build suffix). '~> 1.5' → (1, 5); 'v1.7.2' → (1, 7, 2)."""
    m = re.search(r"(\d+(?:\.\d+)*)", s)
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def is_stable(tag: str) -> bool:
    return not re.search(r"(a|b|rc|alpha|beta|dev|pre)\d*", tag, re.IGNORECASE)


def cmp_status(pinned: str, latest: str) -> str:
    """UP-TO-DATE / BEHIND / REVIEW from two version strings (compared zero-padded)."""
    if latest.startswith("<") or not vtuple(latest):
        return "REVIEW"
    pt, lt = vtuple(pinned), vtuple(latest)
    if not pt:
        return "REVIEW"
    n = max(len(pt), len(lt))
    pt += (0,) * (n - len(pt))
    lt += (0,) * (n - len(lt))
    if lt > pt:
        return "BEHIND"
    return "UP-TO-DATE"


# ── upstream fetchers (each returns the latest stable version string) ─────────


def _get_json(url: str) -> dict:
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "versions-upstream"}
    )
    if "api.github.com" in url and (tok := os.environ.get("GITHUB_TOKEN")):
        req.add_header("Authorization", f"Bearer {tok}")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.load(resp)


def pypi_latest(pkg: str) -> str:
    data = _get_json(f"https://pypi.org/pypi/{pkg}/json")
    stable = [v for v in data.get("releases", {}) if is_stable(v) and vtuple(v)]
    return max(stable, key=vtuple) if stable else data["info"]["version"]


def rubygems_latest(pkg: str) -> str:
    data = _get_json(f"https://rubygems.org/api/v1/gems/{pkg}.json")
    return data["version"]


def dockerhub_latest(repo: str, major: int | None = None) -> str:
    """Max semver tag in a Docker Hub repo (covers images AND OCI Helm charts).

    `major` constrains candidates to one major line — needed for the worker-controller
    repo, which carries BOTH the chart (0.x) and the controller image (1.x) as tags;
    without it max() would compare the chart pin against the image tag. (Revisit the
    pinned-major filter if/when the chart line itself reaches a new major.)
    """
    data = _get_json(
        f"https://hub.docker.com/v2/repositories/{repo}/tags?page_size=100&ordering=last_updated"
    )
    tags = [
        t["name"]
        for t in data.get("results", [])
        if re.fullmatch(r"v?\d+\.\d+(\.\d+)?", t["name"])
        and is_stable(t["name"])
        and (major is None or vtuple(t["name"])[0] == major)
    ]
    if not tags:
        raise ValueError("no semver tags found")
    return max(tags, key=vtuple)


def github_latest_release(repo: str) -> str:
    return _get_json(f"https://api.github.com/repos/{repo}/releases/latest")["tag_name"]


def nuget_latest(package: str) -> str:
    data = _get_json(
        f"https://api.nuget.org/v3-flatcontainer/{package.lower()}/index.json"
    )
    versions = [v for v in data.get("versions", []) if is_stable(v) and vtuple(v)]
    return max(versions, key=vtuple) if versions else data["versions"][-1]


def tfregistry_latest(provider: str) -> str:
    return _get_json(f"https://registry.terraform.io/v1/providers/{provider}")[
        "version"
    ]


# ── checks ─────────────────────────────────────────────────────────────────--


def main() -> None:
    strict = "--strict" in sys.argv[1:]
    t = yaml.safe_load(MANIFEST.read_text())["temporal"]

    # (component, pinned-display, fetcher, source-label)
    checks = [
        (
            "temporalio (SDK)",
            t["python_sdk"],
            lambda: pypi_latest("temporalio"),
            "PyPI",
        ),
        (
            "temporal Ruby SDK",
            t["ruby_sdk"],
            lambda: rubygems_latest("temporalio"),
            "RubyGems",
        ),
        (
            "temporal .NET SDK",
            t["dotnet_sdk"],
            lambda: nuget_latest("Temporalio"),
            "NuGet",
        ),
        (
            "temporal server",
            t["server"],
            lambda: dockerhub_latest("temporalio/server"),
            "Docker Hub",
        ),
        (
            "temporal admin-tools",
            t["admin_tools"],
            lambda: dockerhub_latest("temporalio/admin-tools"),
            "Docker Hub",
        ),
        (
            "temporal UI",
            t["ui"],
            lambda: dockerhub_latest("temporalio/ui"),
            "Docker Hub",
        ),
        (
            "temporal CLI",
            t["cli"],
            lambda: github_latest_release("temporalio/cli"),
            "GitHub",
        ),
        (
            "temporalcloud TF provider",
            t["cloud_tf_provider"],
            lambda: tfregistry_latest("temporalio/temporalcloud"),
            "TF Registry",
        ),
        (
            "worker-controller chart",
            t["worker_controller_chart"],
            lambda: dockerhub_latest(
                "temporalio/temporal-worker-controller",
                major=vtuple(str(t["worker_controller_chart"]))[0],
            ),
            "Docker Hub",
        ),
    ]

    rows = []
    behind = 0
    for component, pinned, fetch, source in checks:
        try:
            latest = fetch()
            status = cmp_status(str(pinned), latest)
        except (
            urllib.error.URLError,
            ValueError,
            KeyError,
            TimeoutError,
            json.JSONDecodeError,
        ) as e:
            latest, status = f"<error: {type(e).__name__}>", "ERROR"
        if status == "BEHIND":
            behind += 1
        rows.append((component, str(pinned), latest, status, source))

    cw = max(len(r[0]) for r in rows)
    pw = max(len(r[1]) for r in rows)
    lw = max(len(r[2]) for r in rows)
    print("Tier-1 Temporal — pinned vs upstream latest stable\n")
    for component, pinned, latest, status, source in rows:
        print(
            f"  [{status:<10}] {component:<{cw}}  pinned={pinned:<{pw}}  latest={latest:<{lw}}  ({source})"
        )

    print()
    if behind:
        print(
            f"{behind} component(s) BEHIND upstream latest stable. Bump in config/dependencies.yaml, then `just versions-audit`."
        )
    else:
        print(
            "All Tier-1 components at upstream latest stable (or newer/under review)."
        )

    sys.exit(1 if (strict and behind) else 0)


if __name__ == "__main__":
    main()
