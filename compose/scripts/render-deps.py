#!/usr/bin/env python3
"""Render config/dependencies.yaml to a shell-sourceable env file.

The single source of truth for third-party dependency versions is
config/dependencies.yaml. Terraform reads it directly (yamldecode); the bash
delivery scripts (deploy/kind/*.sh) can't, so this renders the scalars they need
to config/.generated/deps.env (git-ignored), which they `source`. Same pattern as
compose/scripts/render-oss-bootstrap.py.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC = REPO_ROOT / "config" / "dependencies.yaml"
OUT = REPO_ROOT / "config" / ".generated" / "deps.env"


def main() -> None:
    spec = yaml.safe_load(SPEC.read_text())
    charts = spec["charts"]
    lines = [
        "# Generated from config/dependencies.yaml by render-deps.py. Do not edit.",
        f"ZOT_VERSION={spec['registry']['zot_version']}",
        f"CERT_MANAGER_VERSION={charts['cert-manager']['version']}",
        f"CERT_MANAGER_REPO={charts['cert-manager']['repo']}",
        # CRDs and controller share one version.
        f"WORKER_CONTROLLER_VERSION={charts['temporal-worker-controller']['version']}",
        f"ARGOCD_CHART_VERSION={charts['argo-cd']['version']}",
        f"CNPG_VERSION={charts['cloudnative-pg']['version']}",
        f"CNPG_REPO={charts['cloudnative-pg']['repo']}",
        f"NGINX_IMAGE={spec['images']['nginx']['repository']}:{spec['images']['nginx']['tag']}",
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n")
    print(f"Rendered {OUT.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
