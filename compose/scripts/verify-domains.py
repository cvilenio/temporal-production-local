#!/usr/bin/env python3
"""Domain doctor — verify config/domains/*.yaml against code, manifests, and platform wiring.

Each descriptor is the single source of truth for a business domain. This script fails loud
and early on drift before image build or cluster apply.

Usage:
  verify-domains.py              # all descriptors
  verify-domains.py orders       # one domain (filename stem or domain key)

Run via `just verify-domains` / `just verify-domain NAME` (wired into `just lint`).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tomllib
from pathlib import Path

import yaml
from appkit.domains import temporal_namespace_from_descriptor

REPO_ROOT = Path(
    os.environ.get("DOMAIN_VERIFY_ROOT", Path(__file__).resolve().parents[2])
).resolve()
NAMESPACES = REPO_ROOT / "config" / "temporal" / "namespaces.yaml"
DOMAINS_DIR = REPO_ROOT / "config" / "domains"
CLUSTER_VARS = REPO_ROOT / "deploy/terraform/layers/cluster/variables.tf"
PYPROJECT = REPO_ROOT / "pyproject.toml"
SETTINGS_GRADLE = REPO_ROOT / "settings.gradle"

SUPPORTED_LANGUAGES = ("python", "java", "go", "typescript", "ruby", "dotnet")

# Worker languages that are self-contained per worker dir (no libs/<domain>/ kernel).
LANGS_WITHOUT_KERNEL_LIB = frozenset({"go", "dotnet"})

WORKER_KNOWN_FIELDS = frozenset(
    {
        "profile",
        "language",
        "kind",
        "task_queue",
        "deployment_name",
        "dependency_group",
        "dockerfile",
        "replicas",
        "startup_probe",
        "extra_env",
        "runtime_version",
        "autoscaling",
    }
)

AUTOSCALING_KNOWN_FIELDS = frozenset(
    {
        "minReplicas",
        "maxReplicas",
        "targetBacklogPerReplica",
        "slotScaleUpEnabled",
        "slotScaleDownGateEnabled",
        "behavior",
        "targetSlotUtilizationPercent",
        "scaleDownSlotUtilizationPercent",
        "slotUpWindowSeconds",
        "slotDownWindowSeconds",
    }
)

_PY_TASK_QUEUE_RE = re.compile(r'=\s*"([^"]+-task-queue)"')
_JAVA_TASK_QUEUE_RE = re.compile(r'=\s*"([^"]+-task-queue)";\s*$', re.MULTILINE)
_RUBY_TASK_QUEUE_RE = re.compile(r"=\s*'([^']+-task-queue)'")
_DOTNET_TASK_QUEUE_RE = re.compile(r'=\s*"([^"]+-task-queue)";\s*$', re.MULTILINE)
_CHART_VERSION_RE = re.compile(
    r'^version:\s*["\']?([0-9]+(?:\.[0-9]+)*)(?:["\']|$)', re.MULTILINE
)


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text()) or {}


def parse_version(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for piece in version.strip().split("."):
        if not piece.isdigit():
            raise ValueError(f"non-numeric version segment: {piece!r}")
        parts.append(int(piece))
    return tuple(parts)


def version_gte(left: str, right: str) -> bool:
    return parse_version(left) >= parse_version(right)


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def descriptor_path(domain: str) -> Path | None:
    by_name = DOMAINS_DIR / f"{domain}.yaml"
    if by_name.is_file():
        return by_name
    if not DOMAINS_DIR.is_dir():
        return None
    for path in sorted(DOMAINS_DIR.glob("*.yaml")):
        desc = load_yaml(path)
        if desc.get("domain") == domain:
            return path
    return None


def tf_variable_default(tf_text: str, var_name: str) -> str | None:
    """Read default = \"...\" from a Terraform variable block (brace-aware)."""
    needle = f'variable "{var_name}"'
    start = tf_text.find(needle)
    if start < 0:
        return None
    brace_start = tf_text.find("{", start)
    if brace_start < 0:
        return None
    depth = 0
    for i in range(brace_start, len(tf_text)):
        ch = tf_text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                block = tf_text[brace_start : i + 1]
                match = re.search(r'default\s*=\s*"([^"]+)"', block)
                return match.group(1) if match else None
    return None


def pyproject_groups(text: str) -> set[str]:
    data = tomllib.loads(text)
    return set((data.get("dependency-groups") or {}).keys())


def pyproject_members(text: str) -> set[str]:
    data = tomllib.loads(text)
    tool = data.get("tool") or {}
    uv = tool.get("uv") or {}
    workspace = uv.get("workspace") or {}
    members = workspace.get("members") or []
    return {str(m) for m in members}


def worker_dir(domain: str, language: str, profile: str) -> Path:
    return REPO_ROOT / "apps/temporal/workers" / language / domain / profile


def python_task_queues(domain: str) -> set[str]:
    path = REPO_ROOT / f"libs/{domain}/python/{domain}/shared/temporal_ids.py"
    if not path.is_file():
        raise FileNotFoundError(
            f"add libs/{domain}/python/{domain}/shared/temporal_ids.py with TaskQueue constants"
        )
    return set(_PY_TASK_QUEUE_RE.findall(path.read_text()))


def java_task_queues(domain: str) -> set[str]:
    shared = REPO_ROOT / f"libs/{domain}/java"
    if not shared.is_dir():
        raise FileNotFoundError(
            f"add Java task-queue constants under libs/{domain}/java/"
        )
    candidates = list(shared.rglob("shared/*Ids.java")) + list(
        shared.rglob("**/TemporalIds.java")
    )
    if not candidates:
        raise FileNotFoundError(
            f"add *Ids.java under libs/{domain}/java/ with task-queue constants"
        )
    queues: set[str] = set()
    for path in candidates:
        queues.update(_JAVA_TASK_QUEUE_RE.findall(path.read_text()))
    return queues


def ruby_task_queues(domain: str) -> set[str]:
    path = (
        REPO_ROOT
        / f"libs/{domain}/ruby/lib/{domain.replace('-', '_')}_lib/temporal_ids.rb"
    )
    if not path.is_file():
        candidates = list((REPO_ROOT / f"libs/{domain}/ruby").rglob("temporal_ids.rb"))
        if not candidates:
            raise FileNotFoundError(
                f"add libs/{domain}/ruby/.../temporal_ids.rb with task-queue constants"
            )
        path = candidates[0]
    return set(_RUBY_TASK_QUEUE_RE.findall(path.read_text()))


def dotnet_task_queues(domain: str) -> set[str]:
    path = REPO_ROOT / f"apps/temporal/workers/dotnet/{domain}/TemporalIds.cs"
    if not path.is_file():
        raise FileNotFoundError(
            f"add apps/temporal/workers/dotnet/{domain}/TemporalIds.cs with task-queue constants"
        )
    return set(_DOTNET_TASK_QUEUE_RE.findall(path.read_text()))


def code_task_queues(domain: str, languages: set[str]) -> set[str]:
    queues: set[str] = set()
    if "python" in languages:
        queues |= python_task_queues(domain)
    if "java" in languages:
        queues |= java_task_queues(domain)
    if "ruby" in languages:
        queues |= ruby_task_queues(domain)
    if "dotnet" in languages:
        queues |= dotnet_task_queues(domain)
    return queues


def collect_descriptor_queues(descriptor: dict) -> set[str]:
    queues: set[str] = set()
    for worker in descriptor.get("workers") or []:
        if tq := worker.get("task_queue"):
            queues.add(str(tq))
    for wf in descriptor.get("workflows") or []:
        if tq := wf.get("task_queue"):
            queues.add(str(tq))
    return queues


def gradle_includes_worker(worker_path: Path) -> bool:
    if not SETTINGS_GRADLE.is_file():
        return False
    text = SETTINGS_GRADLE.read_text()
    needle = f"file('{worker_path.relative_to(REPO_ROOT).as_posix()}')"
    return needle in text


def worker_has_entrypoint(worker_path: Path, language: str) -> bool:
    lang = language.lower()
    if lang == "python":
        return (worker_path / "main.py").is_file()
    if lang == "java":
        return (worker_path / "build.gradle").is_file() or any(
            worker_path.rglob("*Application.java")
        )
    if lang == "go":
        return (worker_path / "go.mod").is_file() or any(worker_path.rglob("main.go"))
    if lang == "typescript":
        return (worker_path / "package.json").is_file()
    if lang == "ruby":
        return (worker_path / "worker.rb").is_file() and (
            worker_path / "Gemfile"
        ).is_file()
    if lang == "dotnet":
        return (worker_path / "Program.cs").is_file() and any(
            worker_path.glob("*.csproj")
        )
    return False


def python_worker_registered(
    domain: str, group: str, pyproject_text: str
) -> tuple[bool, str]:
    groups = pyproject_groups(pyproject_text)
    members = pyproject_members(pyproject_text)
    member = f"libs/{domain}/python"
    if group not in groups:
        return False, f"add [{group}] under [dependency-groups] in pyproject.toml"
    if member not in members:
        return False, f"add {member!r} to [tool.uv.workspace].members in pyproject.toml"
    return True, ""


def chart_version_for_domain(domain: str) -> tuple[str | None, str | None]:
    chart = REPO_ROOT / "deploy/charts" / f"{domain}-workers/Chart.yaml"
    if not chart.is_file():
        return None, None
    text = chart.read_text()
    match = _CHART_VERSION_RE.search(text)
    chart_ver = match.group(1) if match else None
    tf_var = f"{domain.replace('-', '_')}_workers_chart_version"
    tf_default: str | None = None
    if CLUSTER_VARS.is_file():
        tf_default = tf_variable_default(CLUSTER_VARS.read_text(), tf_var)
    return chart_ver, tf_default


def grafana_dashboard_path(domain: str) -> Path:
    return (
        REPO_ROOT
        / "compose/observability/grafana/dashboards"
        / domain
        / f"{domain}.json"
    )


def verify_descriptor(
    path: Path, namespace_domains: set[str]
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    rel_path = rel(path)
    descriptor = load_yaml(path)
    domain = descriptor.get("domain")
    if not domain:
        return [f"{rel_path}: missing required field 'domain'"], warnings
    domain = str(domain)
    stem = path.stem
    if domain != stem:
        errors.append(
            f"{rel_path}: 'domain' must match filename ({stem!r}); set domain: {stem} or rename file"
        )

    if descriptor.get("kernel"):
        errors.append(
            f"{rel_path}: remove obsolete 'kernel' field — use domain: {domain!r} for libs/{domain}/"
        )

    if descriptor.get("autoscaling"):
        errors.append(
            f"{rel_path}: remove obsolete top-level 'autoscaling' field - "
            f"nest autoscaling under each workers[] entry"
        )

    if descriptor.get("language") and not all(
        w.get("language") for w in (descriptor.get("workers") or [])
    ):
        errors.append(
            f"{rel_path}: move top-level 'language' to each workers[] entry and remove the top-level field"
        )

    namespace = temporal_namespace_from_descriptor(descriptor)
    if namespace not in namespace_domains:
        errors.append(
            f"{rel_path}: namespace {namespace!r} not in config/temporal/namespaces.yaml — "
            f"add a {namespace}: entry or fix namespace:"
        )

    workers = descriptor.get("workers") or []
    if not workers:
        errors.append(f"{rel_path}: workers[] is empty — add at least one worker entry")
        return errors, warnings

    worker_languages_pre = {
        str(w.get("language", "")).lower() for w in workers if w.get("language")
    }
    libs_dir = REPO_ROOT / "libs" / domain
    if worker_languages_pre - LANGS_WITHOUT_KERNEL_LIB and not libs_dir.is_dir():
        errors.append(
            f"{rel_path}: missing libs/{domain}/ — add the domain package before deploying"
        )

    pyproject_text = PYPROJECT.read_text() if PYPROJECT.is_file() else ""

    worker_languages: set[str] = set()
    workflow_queues: set[str] = set()
    worker_queues: set[str] = set()

    for worker in workers:
        profile = worker.get("profile")
        language = worker.get("language")
        kind = worker.get("kind")
        task_queue = worker.get("task_queue")
        if not profile:
            errors.append(f"{rel_path}: workers[] entry missing 'profile'")
            continue
        if not language:
            errors.append(
                f"{rel_path}: workers[{profile!r}] missing 'language' — add language: python|java|go|typescript"
            )
            continue
        if not kind:
            errors.append(f"{rel_path}: workers[{profile!r}] missing 'kind'")
            continue
        if not task_queue:
            errors.append(f"{rel_path}: workers[{profile!r}] missing 'task_queue'")
            continue

        unknown_fields = sorted(set(worker) - WORKER_KNOWN_FIELDS)
        if unknown_fields:
            errors.append(
                f"{rel_path}: workers[{profile!r}] unknown field(s) {unknown_fields} — "
                f"fix typo or extend verify-domains WORKER_KNOWN_FIELDS"
            )

        lang = str(language).lower()
        worker_languages.add(lang)
        worker_queues.add(str(task_queue))
        if kind == "workflow":
            workflow_queues.add(str(task_queue))

        if lang not in SUPPORTED_LANGUAGES:
            errors.append(
                f"{rel_path}: workers[{profile!r}] language {language!r} unsupported — "
                f"use one of {SUPPORTED_LANGUAGES}"
            )
            continue

        dockerfile = worker.get("dockerfile") or f"images/{lang}.Dockerfile"
        docker_path = REPO_ROOT / dockerfile
        if not docker_path.is_file():
            errors.append(
                f"{rel_path}: workers[{profile!r}] Dockerfile missing at {dockerfile} — "
                f"add images/{lang}.Dockerfile or set dockerfile: on the worker"
            )

        wdir = worker_dir(domain, lang, str(profile))
        if not wdir.is_dir():
            errors.append(
                f"{rel_path}: worker dir missing {rel(wdir)}/ — "
                f"scaffold or move code to apps/temporal/workers/{lang}/{domain}/{profile}/"
            )
        elif not worker_has_entrypoint(wdir, lang):
            errors.append(
                f"{rel_path}: worker dir {rel(wdir)}/ has no entrypoint — "
                f"add main.py (python), build.gradle (java), go.mod (go), worker.rb+Gemfile (ruby), or Program.cs+.csproj (dotnet)"
            )

        if lang == "python":
            dep_group = str(worker.get("dependency_group") or f"{domain}-workers")
            ok, fix = python_worker_registered(domain, dep_group, pyproject_text)
            if not ok:
                errors.append(f"{rel_path}: workers[{profile!r}] {fix}")
        elif lang == "java":
            if not gradle_includes_worker(wdir):
                errors.append(
                    f"{rel_path}: workers[{profile!r}] not in settings.gradle — "
                    f"add include + projectDir -> {rel(wdir)}"
                )
        elif lang == "go":
            if not (wdir / "go.mod").is_file():
                errors.append(
                    f"{rel_path}: workers[{profile!r}] missing go.mod in {rel(wdir)}/"
                )

        autoscaling = worker.get("autoscaling")
        if autoscaling is not None:
            if not isinstance(autoscaling, dict):
                errors.append(
                    f"{rel_path}: workers[{profile!r}] autoscaling must be a mapping"
                )
            else:
                unknown_autoscaling = sorted(
                    set(autoscaling) - AUTOSCALING_KNOWN_FIELDS
                )
                if unknown_autoscaling:
                    errors.append(
                        f"{rel_path}: workers[{profile!r}] autoscaling unknown field(s) "
                        f"{unknown_autoscaling} - fix typo or extend "
                        f"verify-domains AUTOSCALING_KNOWN_FIELDS"
                    )

                min_replicas = autoscaling.get("minReplicas")
                max_replicas = autoscaling.get("maxReplicas")
                if min_replicas is None:
                    errors.append(
                        f"{rel_path}: workers[{profile!r}] autoscaling missing minReplicas"
                    )
                elif not isinstance(min_replicas, int) or min_replicas < 1:
                    errors.append(
                        f"{rel_path}: workers[{profile!r}] autoscaling minReplicas must be >= 1"
                    )
                if max_replicas is None:
                    errors.append(
                        f"{rel_path}: workers[{profile!r}] autoscaling missing maxReplicas"
                    )
                elif not isinstance(max_replicas, int) or max_replicas < 1:
                    errors.append(
                        f"{rel_path}: workers[{profile!r}] autoscaling maxReplicas must be >= 1"
                    )
                if (
                    isinstance(min_replicas, int)
                    and isinstance(max_replicas, int)
                    and min_replicas > max_replicas
                ):
                    errors.append(
                        f"{rel_path}: workers[{profile!r}] autoscaling minReplicas "
                        f"({min_replicas}) must be <= maxReplicas ({max_replicas})"
                    )

            if worker.get("replicas") is not None:
                warnings.append(
                    f"{rel_path}: workers[{profile!r}] has both replicas and autoscaling - "
                    f"replicas is ignored; minReplicas is the floor"
                )

    try:
        code_queues = code_task_queues(domain, worker_languages)
    except FileNotFoundError as exc:
        errors.append(f"{rel_path}: {exc}")
        code_queues = set()

    desc_queues = collect_descriptor_queues(descriptor)
    if not desc_queues:
        errors.append(f"{rel_path}: no task_queue values on workers or workflows")

    for tq in sorted(desc_queues):
        if code_queues and tq not in code_queues:
            errors.append(
                f"{rel_path}: task_queue {tq!r} not in libs/{domain}/ TaskQueue constants "
                f"{sorted(code_queues)} — add the constant or fix the descriptor"
            )

    for tq in sorted(desc_queues - worker_queues):
        errors.append(
            f"{rel_path}: orphan task_queue {tq!r} — no workers[] entry polls this queue"
        )

    for wf in descriptor.get("workflows") or []:
        wf_type = wf.get("type") or "<unknown>"
        wf_queue = str(wf.get("task_queue") or "")
        if wf_queue and wf_queue not in workflow_queues:
            errors.append(
                f"{rel_path}: workflows[{wf_type!r}] task_queue {wf_queue!r} is not served by "
                f"any kind: workflow worker — add a workflow worker on that queue"
            )
        if not wf.get("sample_inputs"):
            warnings.append(
                f"{rel_path}: workflows[{wf_type!r}] has no sample_inputs — "
                f"won't appear in console trigger"
            )

    chart_dir = REPO_ROOT / "deploy/charts" / f"{domain}-workers"
    if not chart_dir.is_dir():
        errors.append(
            f"{rel_path}: missing chart deploy/charts/{domain}-workers/ — run scaffold-domain"
        )
    else:
        chart_ver, tf_default = chart_version_for_domain(domain)
        if chart_ver is None:
            errors.append(
                f"{rel_path}: deploy/charts/{domain}-workers/Chart.yaml missing version:"
            )
        elif tf_default is None:
            errors.append(
                f"{rel_path}: add variable {domain.replace('-', '_')}_workers_chart_version "
                f"to deploy/terraform/layers/cluster/variables.tf"
            )
        elif not version_gte(chart_ver, tf_default):
            errors.append(
                f"{rel_path}: chart version {chart_ver} < cluster default {tf_default} — "
                f"bump deploy/charts/{domain}-workers/Chart.yaml and variables.tf "
                f"{domain.replace('-', '_')}_workers_chart_version (stale-chart trap; ADR-0011)"
            )

    if (descriptor.get("observability") or {}).get("dashboard"):
        dash = grafana_dashboard_path(domain)
        if not dash.is_file():
            errors.append(
                f"{rel_path}: observability.dashboard=true but missing {rel(dash)} — "
                f"scaffold the Grafana dashboard or set observability.dashboard: false"
            )

    return errors, warnings


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Domain doctor for config/domains/*.yaml"
    )
    parser.add_argument(
        "domain",
        nargs="?",
        help="Verify one domain (filename stem or domain key). Default: all.",
    )
    args = parser.parse_args()

    if not DOMAINS_DIR.is_dir():
        print(f"OK: no {rel(DOMAINS_DIR)}/ directory yet.")
        sys.exit(0)

    if args.domain:
        path = descriptor_path(args.domain)
        if path is None:
            print(
                f"FAIL: no descriptor for domain {args.domain!r} under {rel(DOMAINS_DIR)}/"
            )
            sys.exit(1)
        domain_files = [path]
    else:
        domain_files = sorted(DOMAINS_DIR.glob("*.yaml"))
        if not domain_files:
            print(f"OK: no domain descriptors in {rel(DOMAINS_DIR)}/.")
            sys.exit(0)

    ns_spec = load_yaml(NAMESPACES)
    namespace_domains = set((ns_spec.get("domains") or {}).keys())

    all_errors: list[str] = []
    all_warnings: list[str] = []
    for path in domain_files:
        errors, warnings = verify_descriptor(path, namespace_domains)
        all_errors.extend(errors)
        all_warnings.extend(warnings)

    if all_warnings:
        print("WARN: domain verification:")
        for warn in all_warnings:
            print(f"  - {warn}")

    if all_errors:
        print("FAIL: domain verification:")
        for err in all_errors:
            print(f"  - {err}")
        sys.exit(1)

    names = ", ".join(p.stem for p in domain_files)
    print(f"OK: {len(domain_files)} domain descriptor(s) verified ({names}).")
    sys.exit(0)


if __name__ == "__main__":
    main()
