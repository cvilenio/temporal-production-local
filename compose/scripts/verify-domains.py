#!/usr/bin/env python3
"""Verify config/domains/*.yaml against namespace spec and language kernels.

Each domain descriptor's `domain` key MUST exist in config/temporal/namespaces.yaml.
Every `task_queue` on workers and workflows MUST match a TaskQueue constant declared
in that domain's language kernel (libs/<kernel>/.../shared/temporal_ids for Python).

Run via `just verify-domains` (wired into `just lint`). Offline, stdlib + pyyaml only.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(
    os.environ.get("DOMAIN_VERIFY_ROOT", Path(__file__).resolve().parents[2])
).resolve()
NAMESPACES = REPO_ROOT / "config" / "temporal" / "namespaces.yaml"
DOMAINS_DIR = REPO_ROOT / "config" / "domains"

# StrEnum member = "<queue-name>" in temporal_ids.py (Python kernel).
_PY_TASK_QUEUE_RE = re.compile(r'=\s*"([^"]+-task-queue)"')

# public static final String ... = "<queue-name>"; (Java kernel, future).
_JAVA_TASK_QUEUE_RE = re.compile(r'=\s*"([^"]+-task-queue)";\s*$', re.MULTILINE)


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text()) or {}


def kernel_name(descriptor: dict, path: Path) -> str:
    domain = descriptor.get("domain")
    if not domain:
        raise ValueError(f"{path}: missing required field 'domain'")
    return str(descriptor.get("kernel") or domain)


def python_task_queues(kernel: str) -> set[str]:
    rel = f"libs/{kernel}/python/{kernel}/shared/temporal_ids.py"
    path = REPO_ROOT / rel
    if not path.exists():
        raise FileNotFoundError(f"Python kernel not found: {rel}")
    return set(_PY_TASK_QUEUE_RE.findall(path.read_text()))


def java_task_queues(kernel: str) -> set[str]:
    shared = REPO_ROOT / f"libs/{kernel}/java"
    if not shared.is_dir():
        raise FileNotFoundError(f"Java kernel not found under libs/{kernel}/java/")
    # Convention: TemporalIds.java or any *Ids.java under shared/
    candidates = list(shared.rglob("shared/*Ids.java")) + list(
        shared.rglob("**/TemporalIds.java")
    )
    if not candidates:
        raise FileNotFoundError(
            f"No *Ids.java under libs/{kernel}/java/ (expected task-queue constants)"
        )
    queues: set[str] = set()
    for path in candidates:
        queues.update(_JAVA_TASK_QUEUE_RE.findall(path.read_text()))
    return queues


def kernel_task_queues(language: str, kernel: str) -> set[str]:
    lang = language.lower()
    if lang == "python":
        return python_task_queues(kernel)
    if lang == "java":
        return java_task_queues(kernel)
    raise ValueError(f"unsupported language {language!r} (expected python or java)")


def collect_descriptor_queues(descriptor: dict) -> set[str]:
    queues: set[str] = set()
    for worker in descriptor.get("workers") or []:
        if tq := worker.get("task_queue"):
            queues.add(str(tq))
    for wf in descriptor.get("workflows") or []:
        if tq := wf.get("task_queue"):
            queues.add(str(tq))
    return queues


def verify_descriptor(path: Path, namespace_domains: set[str]) -> list[str]:
    errors: list[str] = []
    descriptor = load_yaml(path)
    domain = descriptor.get("domain")
    if not domain:
        return [f"{path.relative_to(REPO_ROOT)}: missing 'domain'"]
    rel = path.relative_to(REPO_ROOT)

    if domain not in namespace_domains:
        errors.append(
            f"{rel}: domain {domain!r} not in config/temporal/namespaces.yaml"
        )

    language = descriptor.get("language")
    if not language:
        errors.append(f"{rel}: missing 'language'")
        return errors

    try:
        kernel = kernel_name(descriptor, path)
        kernel_queues = kernel_task_queues(language, kernel)
    except (FileNotFoundError, ValueError) as exc:
        errors.append(f"{rel}: {exc}")
        return errors

    desc_queues = collect_descriptor_queues(descriptor)
    if not desc_queues:
        errors.append(f"{rel}: no task_queue values on workers or workflows")

    for tq in sorted(desc_queues):
        if tq not in kernel_queues:
            errors.append(
                f"{rel}: task_queue {tq!r} not in kernel "
                f"libs/{kernel}/ ({language}) constants {sorted(kernel_queues)}"
            )

    unused = kernel_queues - desc_queues
    if unused:
        errors.append(
            f"{rel}: kernel defines task queues not listed in descriptor: "
            f"{sorted(unused)}"
        )

    return errors


def main() -> None:
    if not DOMAINS_DIR.is_dir():
        print(f"OK: no {DOMAINS_DIR.relative_to(REPO_ROOT)}/ directory yet.")
        sys.exit(0)

    domain_files = sorted(DOMAINS_DIR.glob("*.yaml"))
    if not domain_files:
        print(f"OK: no domain descriptors in {DOMAINS_DIR.relative_to(REPO_ROOT)}/.")
        sys.exit(0)

    ns_spec = load_yaml(NAMESPACES)
    namespace_domains = set((ns_spec.get("domains") or {}).keys())

    all_errors: list[str] = []
    for path in domain_files:
        all_errors.extend(verify_descriptor(path, namespace_domains))

    if all_errors:
        print("FAIL: domain descriptor verification:")
        for err in all_errors:
            print(f"  - {err}")
        print(
            "\nFix: align config/domains/*.yaml with namespaces.yaml and kernel TaskQueue constants."
        )
        sys.exit(1)

    names = ", ".join(p.stem for p in domain_files)
    print(f"OK: {len(domain_files)} domain descriptor(s) verified ({names}).")
    sys.exit(0)


if __name__ == "__main__":
    main()
