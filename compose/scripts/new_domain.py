#!/usr/bin/env python3
"""Write a commented starter config/domains/<domain>.yaml for human editing.

Usage:
  just new-domain mydomain
  uv run python compose/scripts/new_domain.py --name mydomain
"""

from __future__ import annotations

import argparse
import re
import sys
import textwrap
from pathlib import Path

SCRIPT_REPO = Path(__file__).resolve().parents[2]


def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def starter_descriptor(domain: str) -> str:
    return textwrap.dedent(
        f"""\
        # Domain descriptor — edit this file, then run:
        #   just scaffold-domain {domain}
        #   just verify-domain {domain}
        #   just adopt-domain {domain}
        #
        # See config/domains/orders.yaml for the full schema.

        domain: {domain}
        # namespace: {domain}          # optional Temporal namespace override
        k8s_namespace: orders         # shared kind namespace (mTLS secret)
        data_converter: default

        workers:
          - profile: workflow
            language: python
            kind: workflow
            deployment_name: {domain}-workflow-python
            task_queue: {domain}-workflow-task-queue
          - profile: activity
            language: python
            kind: activity
            deployment_name: {domain}-activity-python
            task_queue: {domain}-activity-task-queue

        workflows:
          - type: HelloWorkflow
            task_queue: {domain}-workflow-task-queue
            sample_inputs:
              - label: happy_path
                input:
                  name: Temporal

        observability:
          dashboard: true
        """
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a starter domain descriptor stub"
    )
    parser.add_argument("--name", required=True, help="domain key (e.g. mydomain)")
    parser.add_argument(
        "--root",
        type=Path,
        default=SCRIPT_REPO,
        help="repository root (default: script repo)",
    )
    args = parser.parse_args()

    domain = args.name.strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9-]*", domain):
        die("domain name must match [a-z][a-z0-9-]*")

    if domain == "orders":
        die("refusing to overwrite the flagship orders descriptor")

    root = args.root.resolve()
    path = root / "config" / "domains" / f"{domain}.yaml"
    if path.exists():
        die(
            f"{path.relative_to(root)} already exists - edit it in place or delete first"
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(starter_descriptor(domain))
    print(f"Wrote starter descriptor: {path.relative_to(root)}")
    print(f"Next: edit workers/workflows, then `just scaffold-domain {domain}`")


if __name__ == "__main__":
    main()
