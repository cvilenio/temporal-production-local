#!/usr/bin/env python3
"""Fail if a resource references a Secret/ConfigMap that lands in an equal-or-later
ArgoCD sync-wave than the resource itself.

This guards the deadlock class that bit checkpoint 0011: a CNPG `Cluster` (wave -1)
that mounts a credential `Secret` left at the default wave (0) → the Cluster's
initdb fails `secret not found`, never goes healthy, and the whole Application's
sync operation hangs forever (a hung op doesn't self-heal). Per ADR-0016, EXISTENCE
dependencies (a CR needs its Secret/ConfigMap to be admitted/initialised) must be
ordered with sync-waves: the dependency in an EARLIER wave than its consumer.

Scope: one rendered manifest stream (stdin), per chart. A reference to a Secret/
ConfigMap NOT defined in this render is skipped — it's provided out-of-band
(another Application, or Terraform-seeded) and its existence is guaranteed before
this chart syncs (that's the TF-courier / separate-failure-domain pattern, ADR-0016).

Usage:  helm template <chart> | python deploy/check-sync-waves.py [--name <chart>]
Exit 0 = ok, 1 = ordering violation, 2 = usage/parse error.
"""

from __future__ import annotations

import sys

import yaml

WAVE_ANNOTATION = "argocd.argoproj.io/sync-wave"


def wave_of(doc: dict) -> int:
    ann = (doc.get("metadata") or {}).get("annotations") or {}
    try:
        return int(ann.get(WAVE_ANNOTATION, 0))
    except (TypeError, ValueError):
        return 0


def _walk(node, found_secrets: set, found_cms: set) -> None:
    """Recursively collect referenced Secret / ConfigMap names."""
    if isinstance(node, dict):
        for key, val in node.items():
            if (
                key in ("secretKeyRef", "secretRef", "superuserSecret")
                and isinstance(val, dict)
                and val.get("name")
            ):
                found_secrets.add(val["name"])
            # CNPG bootstrap.initdb.secret.name (and similar {secret: {name: ...}}).
            elif key == "secret" and isinstance(val, dict) and val.get("name"):
                found_secrets.add(val["name"])
            elif key == "secretName" and isinstance(val, str):
                found_secrets.add(val)
            elif key == "imagePullSecrets" and isinstance(val, list):
                for item in val:
                    if isinstance(item, dict) and item.get("name"):
                        found_secrets.add(item["name"])
            elif (
                key in ("configMapKeyRef", "configMapRef")
                and isinstance(val, dict)
                and val.get("name")
            ):
                found_cms.add(val["name"])
            elif key == "configMap" and isinstance(val, dict) and val.get("name"):
                found_cms.add(val["name"])
            _walk(val, found_secrets, found_cms)
    elif isinstance(node, list):
        for item in node:
            _walk(item, found_secrets, found_cms)


def main() -> int:
    label = "stdin"
    if "--name" in sys.argv:
        i = sys.argv.index("--name")
        if i + 1 < len(sys.argv):
            label = sys.argv[i + 1]

    docs = [
        d
        for d in yaml.safe_load_all(sys.stdin)
        if isinstance(d, dict) and d.get("kind")
    ]

    # Where each Secret/ConfigMap is DEFINED in this render, and at which wave.
    secret_wave: dict[str, int] = {}
    cm_wave: dict[str, int] = {}
    for d in docs:
        kind = d.get("kind")
        name = (d.get("metadata") or {}).get("name")
        if not name:
            continue
        if kind == "Secret":
            secret_wave[name] = wave_of(d)
        elif kind == "ConfigMap":
            cm_wave[name] = wave_of(d)

    violations: list[str] = []
    for d in docs:
        consumer_kind = d.get("kind")
        consumer_name = (d.get("metadata") or {}).get("name", "?")
        if consumer_kind in ("Secret", "ConfigMap"):
            continue
        cwave = wave_of(d)
        refs_secrets: set[str] = set()
        refs_cms: set[str] = set()
        _walk(d, refs_secrets, refs_cms)

        for s in sorted(refs_secrets):
            if s in secret_wave and secret_wave[s] >= cwave:
                violations.append(
                    f"{consumer_kind}/{consumer_name} (wave {cwave}) references "
                    f"Secret/{s} which is in wave {secret_wave[s]} — the Secret must "
                    f"be in an EARLIER wave (existence dependency; see ADR-0016)."
                )
        for c in sorted(refs_cms):
            if c in cm_wave and cm_wave[c] >= cwave:
                violations.append(
                    f"{consumer_kind}/{consumer_name} (wave {cwave}) references "
                    f"ConfigMap/{c} which is in wave {cm_wave[c]} — the ConfigMap must "
                    f"be in an EARLIER wave (existence dependency; see ADR-0016)."
                )

    if violations:
        print(f"✖ sync-wave ordering violations in {label}:", file=sys.stderr)
        for v in violations:
            print(f"   - {v}", file=sys.stderr)
        return 1
    print(f"   sync-wave ordering ok ({label})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
