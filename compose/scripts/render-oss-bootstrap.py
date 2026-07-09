#!/usr/bin/env python3
"""Render the shared Temporal spec to a shell-friendly file for the OSS bootstrap.

The local OSS bootstrap runs in the temporalio/admin-tools image, which has no
yq/jq. Rather than parse YAML in POSIX sh, the host (which has the dev deps)
renders config/temporal/namespaces.yaml down to a tiny env file the container
just sources and loops over. This keeps Cloud (Terraform reads the YAML
directly) and OSS reading the SAME source of truth — no drift.

Run by `just legacy-up`/`just legacy-fresh` before `docker compose up`. Output is git-ignored.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC = REPO_ROOT / "config" / "temporal" / "namespaces.yaml"
OUT = REPO_ROOT / "config" / "temporal" / ".generated" / "oss-bootstrap.env"

# Domain keys must be shell-safe (alnum + underscore) for OSS_RETENTION_<key> vars.
_SAFE = re.compile(r"^[a-zA-Z0-9_]+$")


def _env_key(domain: str) -> str:
    key = domain.replace("-", "_")
    if not _SAFE.match(key):
        raise ValueError(f"domain {domain!r} is not shell-safe after normalizing")
    return key


def main() -> int:
    spec = yaml.safe_load(SPEC.read_text())
    domains: dict = spec.get("domains") or {}
    if not domains:
        print("FAIL: namespaces.yaml has no domains", file=sys.stderr)
        return 1

    lines = ["# Generated from config/temporal/namespaces.yaml — do not edit."]
    domain_names = sorted(domains.keys())
    lines.append(f'OSS_DOMAINS="{" ".join(domain_names)}"')

    for domain, cfg in sorted(domains.items()):
        env_key = _env_key(domain)
        retention_days = cfg.get("retention_days", 30)
        attrs = " ".join(
            f"{name}={typ}"
            for name, typ in (cfg.get("search_attributes") or {}).items()
        )
        lines.append(f"OSS_RETENTION_{env_key}={retention_days}")
        lines.append(f'OSS_SEARCH_ATTRIBUTES_{env_key}="{attrs}"')

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n")
    print(
        f"Rendered OSS bootstrap for {len(domain_names)} domain(s): "
        f"{', '.join(domain_names)} -> {OUT}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
