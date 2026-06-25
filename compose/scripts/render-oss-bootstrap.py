#!/usr/bin/env python3
"""Render the shared Temporal spec to a shell-friendly file for the OSS bootstrap.

The local OSS bootstrap runs in the temporalio/admin-tools image, which has no
yq/jq. Rather than parse YAML in POSIX sh, the host (which has the dev deps)
renders config/temporal/namespaces.yaml down to a tiny env file the container
just sources and loops over. This keeps Cloud (Terraform reads the YAML
directly) and OSS reading the SAME source of truth — no drift.

Run by `poe up`/`poe fresh` before `docker compose up`. Output is git-ignored.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC = REPO_ROOT / "config" / "temporal" / "namespaces.yaml"
OUT = REPO_ROOT / "config" / "temporal" / ".generated" / "oss-bootstrap.env"

# Local OSS runs one domain (the demo's orders workload). Extend here if the
# local cluster should host more domains.
DOMAIN = "ziggymart"


def main() -> int:
    spec = yaml.safe_load(SPEC.read_text())

    domain = spec["domains"][DOMAIN]
    retention_days = domain["retention_days"]

    # Space-separated NAME=TYPE pairs — trivial for sh to split and loop.
    attrs = " ".join(
        f"{name}={typ}" for name, typ in domain["search_attributes"].items()
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        "# Generated from config/temporal/namespaces.yaml — do not edit.\n"
        f"OSS_NAMESPACE={DOMAIN}\n"
        f"OSS_RETENTION_DAYS={retention_days}\n"
        f'OSS_SEARCH_ATTRIBUTES="{attrs}"\n'
    )
    print(f"Rendered OSS bootstrap for '{DOMAIN}': {attrs} -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
