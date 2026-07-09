#!/usr/bin/env python3
"""Audit that every native version pin matches config/dependencies.yaml.

config/dependencies.yaml is the single source of truth for this repo's dependency
versions (ADR-0025). Some consumers read it directly (the kind/OCI delivery stack —
see render-deps.py + the cluster Terraform layer); others can't (static pyproject
TOML the uv resolver reads, HCL `required_providers`, buf plugin pins, .env, hardcoded
compose image tags). This script is the VERIFY half of that split: it reads each
native pin and asserts it equals the value recorded in the manifest.

Tiers (manifest blocks `temporal:` / `platform:` / `code:`):
  - Tier 1 (Temporal) and Tier 2 (platform): a mismatch FAILS the gate (exit 1).
  - Tier 3 (code deps): a mismatch is a WARNING only (never fails).

Run via `just versions-audit` (wired into `just lint`). Offline, stdlib + pyyaml only.
"""

from __future__ import annotations

import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "config" / "dependencies.yaml"


# Sentinel for a native pin whose file isn't present (e.g. .env is git-ignored, so
# it's absent on a fresh clone / in CI). Such a row is SKIPPED — neither pass nor
# drift — so the gate stays green where the file legitimately doesn't exist.
ABSENT = "<file absent>"


@dataclass
class Row:
    tier: int
    component: str
    expected: str
    actual: str
    location: str  # repo-relative path (+ optional detail)

    @property
    def skipped(self) -> bool:
        return self.actual == ABSENT

    @property
    def ok(self) -> bool:
        return self.expected == self.actual


# ── helpers ──────────────────────────────────────────────────────────────────


def read(rel: str) -> str | None:
    path = REPO_ROOT / rel
    return path.read_text() if path.exists() else None


def dep_specs(deps: list[str], pkg: str) -> list[str]:
    """Return the version spec(s) for `pkg` in a list of PEP 508 dependency strings.

    Splits the package name (and any [extras]) off the front; the remainder is the
    spec, e.g. "sqlalchemy[asyncio]>=2.0.30,<3" -> ">=2.0.30,<3".
    """
    out = []
    for dep in deps:
        m = re.match(r"^([A-Za-z0-9_.\-]+)(\[[^\]]*\])?\s*(.*)$", dep.strip())
        if m and m.group(1).lower() == pkg.lower():
            out.append(m.group(3).strip())
    return out


def pyproject_specs(rel_path: str, pkg: str) -> list[tuple[str, str]]:
    """(spec, location) for every occurrence of `pkg` across a pyproject's
    [project.dependencies] and all [dependency-groups]."""
    path = REPO_ROOT / rel_path
    data = tomllib.loads(path.read_text())
    found: list[tuple[str, str]] = []
    for spec in dep_specs(data.get("project", {}).get("dependencies", []), pkg):
        found.append((spec, f"{rel_path} [project.dependencies]"))
    for group, deps in data.get("dependency-groups", {}).items():
        if isinstance(deps, list):
            for spec in dep_specs(deps, pkg):
                found.append((spec, f"{rel_path} [{group}]"))
    return found


def search1(rel_path: str, pattern: str, *, flags: int = 0) -> tuple[str, str]:
    """First capture group of `pattern` in a file, with the repo-relative location.

    Returns the ABSENT sentinel if the file doesn't exist (git-ignored / fresh clone)."""
    text = read(rel_path)
    if text is None:
        return ABSENT, rel_path
    m = re.search(pattern, text, flags)
    return (m.group(1) if m else "<not found>"), rel_path


def tf_provider_version(rel_path: str, source: str) -> tuple[str, str]:
    """version string of the required_provider whose source == `source`."""
    text = read(rel_path)
    if text is None:
        return ABSENT, f"{rel_path} ({source})"
    m = re.search(
        r'source\s*=\s*"' + re.escape(source) + r'".*?version\s*=\s*"([^"]+)"',
        text,
        re.DOTALL,
    )
    return (m.group(1) if m else "<not found>"), f"{rel_path} ({source})"


def strip_v(s: str) -> str:
    return s[1:] if s.startswith("v") else s


# ── checks ─────────────────────────────────────────────────────────────────--


def build_rows(spec: dict) -> list[Row]:
    t = spec["temporal"]
    p = spec["platform"]
    c = spec["code"]
    rows: list[Row] = []

    # ---- Tier 1: Temporal -----------------------------------------------------
    # Python SDK across all four authored locations.
    sdk = t["python_sdk"]
    sdk_files = [
        "pyproject.toml",
        "libs/orders/python/pyproject.toml",
        "libs/appkit/python/pyproject.toml",
    ]
    for f in sdk_files:
        for actual, loc in pyproject_specs(f, "temporalio"):
            rows.append(Row(1, "temporalio (SDK)", sdk, actual, loc))

    java_sdk = t.get("java_sdk")
    if java_sdk:
        actual, loc = search1(
            "gradle.properties", r"^temporalJavaSdkVersion=(\S+)", flags=re.MULTILINE
        )
        rows.append(
            Row(
                1,
                "temporal Java SDK",
                java_sdk,
                actual,
                f"{loc} (temporalJavaSdkVersion)",
            )
        )

    go_sdk = t.get("go_sdk")
    if go_sdk:
        for rel in sorted(REPO_ROOT.glob("templates/domain/go/**/go.mod")):
            actual, loc = search1(
                rel.relative_to(REPO_ROOT).as_posix(),
                r"go\.temporal\.io/sdk v(\S+)",
            )
            rows.append(
                Row(
                    1,
                    "temporal Go SDK",
                    go_sdk,
                    actual,
                    f"{rel.relative_to(REPO_ROOT)}",
                )
            )

    ts_sdk = t.get("ts_sdk")
    if ts_sdk:
        for rel in sorted(
            REPO_ROOT.glob("templates/domain/typescript/**/package.json")
        ):
            text = read(rel.relative_to(REPO_ROOT).as_posix())
            if not text or "@temporalio/worker" not in text:
                continue
            actual, loc = search1(
                rel.relative_to(REPO_ROOT).as_posix(),
                r'"@temporalio/worker":\s*"([^"]+)"',
            )
            rows.append(
                Row(
                    1,
                    "temporal TypeScript SDK",
                    ts_sdk,
                    actual,
                    f"{rel.relative_to(REPO_ROOT)}",
                )
            )

    # Server / admin-tools / UI tags live in .env.
    for comp, var in [
        ("temporal server", "TEMPORAL_VERSION"),
        ("temporal admin-tools", "TEMPORAL_ADMINTOOLS_VERSION"),
        ("temporal UI", "TEMPORAL_UI_VERSION"),
    ]:
        actual, loc = search1(".env", rf"^{var}=(\S+)", flags=re.MULTILINE)
        key = {
            "temporal server": "server",
            "temporal admin-tools": "admin_tools",
            "temporal UI": "ui",
        }[comp]
        rows.append(Row(1, comp, t[key], actual, f"{loc} ({var})"))

    # Temporal Cloud Terraform provider (two HCL files, kept in lockstep).
    for f in [
        "deploy/terraform/layers/cloud/versions.tf",
        "deploy/terraform/modules/cloud-namespace/versions.tf",
    ]:
        actual, loc = tf_provider_version(f, "temporalio/temporalcloud")
        rows.append(
            Row(1, "temporalcloud TF provider", t["cloud_tf_provider"], actual, loc)
        )

    # buf codegen plugins (python + pyi), both pinned to the same tag.
    buf = "libs/orders/proto/buf.gen.yaml"
    for plugin in ["protocolbuffers/python", "protocolbuffers/pyi"]:
        actual, loc = search1(buf, rf"{re.escape(plugin)}:(\S+)")
        rows.append(Row(1, f"buf {plugin}", t["proto_plugin"], actual, buf))

    # worker-controller chart: manifest mirror must equal the canonical charts: entry.
    charts = spec["charts"]
    rows.append(
        Row(
            1,
            "worker-controller chart (mirror)",
            t["worker_controller_chart"],
            charts["temporal-worker-controller"]["version"],
            "config/dependencies.yaml (charts.temporal-worker-controller.version)",
        )
    )
    rows.append(
        Row(
            1,
            "worker-controller-crds chart",
            t["worker_controller_chart"],
            charts["temporal-worker-controller-crds"]["version"],
            "config/dependencies.yaml (charts.temporal-worker-controller-crds.version)",
        )
    )

    # temporal-server wrapper: its Chart.yaml subchart dependency version must equal
    # the canonical charts.temporal entry, and its appVersion must equal the server pin.
    tsrv_chart = "deploy/charts/temporal-server/Chart.yaml"
    dep_ver, _ = search1(
        tsrv_chart,
        r"-\s*name:\s*temporal\b.*?version:\s*\"?([^\"\n]+)\"?",
        flags=re.DOTALL,
    )
    rows.append(
        Row(
            1,
            "temporal-server subchart dep",
            charts["temporal"]["version"],
            dep_ver.strip(),
            f"{tsrv_chart} (dependencies.temporal.version)",
        )
    )
    app_ver, _ = search1(tsrv_chart, r'appVersion:\s*"?([^"\n]+)"?')
    rows.append(
        Row(
            1,
            "temporal-server appVersion",
            t["server"],
            app_ver.strip(),
            f"{tsrv_chart} (appVersion == temporal.server)",
        )
    )

    # ---- Tier 2: platform -----------------------------------------------------
    # Terraform required_version across the layers + the reusable module.
    for f in [
        "deploy/terraform/layers/cluster/versions.tf",
        "deploy/terraform/layers/cloud/versions.tf",
        "deploy/terraform/modules/cloud-namespace/versions.tf",
    ]:
        actual, loc = search1(f, r'required_version\s*=\s*"([^"]+)"')
        rows.append(
            Row(2, "terraform (required_version)", p["terraform_min"], actual, loc)
        )

    # Non-Temporal TF providers (cluster layer).
    cluster_tf = "deploy/terraform/layers/cluster/versions.tf"
    for name, source in [
        ("kubernetes", "hashicorp/kubernetes"),
        ("helm", "hashicorp/helm"),
        ("kubectl", "alekc/kubectl"),
        ("tls", "hashicorp/tls"),
    ]:
        actual, loc = tf_provider_version(cluster_tf, source)
        rows.append(Row(2, f"TF provider {name}", p["providers"][name], actual, loc))

    # Hardcoded host-side compose image tags (otel_lgtm intentionally unpinned → skip).
    compose = "docker-compose.yml"
    for comp, image in [
        ("prometheus", "prom/prometheus"),
        ("clickhouse", "clickhouse/clickhouse-server"),
        ("otel_collector_contrib", "otel/opentelemetry-collector-contrib"),
    ]:
        actual, loc = search1(compose, rf"image:\s*{re.escape(image)}:(\S+)")
        rows.append(
            Row(2, f"image {comp}", p["images"][comp], actual, f"{compose} ({image})")
        )

    # Alloy chart appVersion + image tag (manifest stores the bare version).
    alloy = strip_v(str(p["alloy"]))
    app_actual, _ = search1(
        "deploy/charts/alloy/Chart.yaml", r'appVersion:\s*"?([^"\n]+)"?'
    )
    rows.append(
        Row(
            2,
            "alloy (Chart appVersion)",
            alloy,
            strip_v(app_actual.strip()),
            "deploy/charts/alloy/Chart.yaml",
        )
    )
    tag_actual, _ = search1("deploy/charts/alloy/values.yaml", r"tag:\s*(\S+)")
    rows.append(
        Row(
            2,
            "alloy (image tag)",
            alloy,
            strip_v(tag_actual),
            "deploy/charts/alloy/values.yaml",
        )
    )

    # PostgreSQL: .env var + the hardcoded host-apptier image tag.
    pg = str(p["postgresql"])
    env_pg, _ = search1(".env", r"^POSTGRESQL_VERSION=(\S+)", flags=re.MULTILINE)
    rows.append(Row(2, "postgresql", pg, env_pg, ".env (POSTGRESQL_VERSION)"))
    ha_pg, _ = search1("compose/host-apptier.yml", r"image:\s*postgres:(\S+)")
    rows.append(Row(2, "postgresql", pg, ha_pg, "compose/host-apptier.yml"))

    # ---- Tier 3: code deps (report-only) -------------------------------------
    # Canonical specs live in the orders kernel; warn on drift anywhere it appears.
    for pkg, key in [
        ("pydantic", "pydantic"),
        ("httpx", "httpx"),
        ("sqlalchemy", "sqlalchemy"),
        ("protobuf", "protobuf"),
    ]:
        expected = c[key]
        for f in [
            "pyproject.toml",
            "libs/orders/python/pyproject.toml",
            "libs/appkit/python/pyproject.toml",
        ]:
            for actual, loc in pyproject_specs(f, pkg):
                rows.append(Row(3, pkg, expected, actual, loc))

    return rows


# ── output ─────────────────────────────────────────────────────────────────--


def render(rows: list[Row]) -> int:
    """Print a grouped table; return process exit code."""
    tier_names = {
        1: "Tier 1 — Temporal",
        2: "Tier 2 — platform",
        3: "Tier 3 — code deps (report-only)",
    }
    hard_fail = 0
    warn = 0
    skip = 0

    for tier in (1, 2, 3):
        group = [r for r in rows if r.tier == tier]
        if not group:
            continue
        print(f"\n=== {tier_names[tier]} ===")
        cw = max(len(r.component) for r in group)
        ew = max(len(r.expected) for r in group)
        aw = max(len(r.actual) for r in group)
        for r in group:
            if r.skipped:
                status = "SKIP"
                skip += 1
            elif r.ok:
                status = "OK"
            elif tier == 3:
                status = "WARN"
                warn += 1
            else:
                status = "DRIFT"
                hard_fail += 1
            print(
                f"  [{status:<5}] {r.component:<{cw}}  manifest={r.expected:<{ew}}  "
                f"native={r.actual:<{aw}}  @ {r.location}"
            )

    print()
    total = len(rows)
    extra = f" ({skip} skipped — file absent)" if skip else ""
    if hard_fail:
        print(
            f"FAIL: {hard_fail} Tier-1/2 drift(s), {warn} Tier-3 warning(s), {total} pins checked{extra}."
        )
        print(
            "Fix: align the native pin(s) above with config/dependencies.yaml (or update the manifest)."
        )
        return 1
    if warn:
        print(
            f"OK (with {warn} Tier-3 warning(s)): {total} pins checked, no Tier-1/2 drift{extra}."
        )
    else:
        print(f"OK: all {total} pins match config/dependencies.yaml{extra}.")
    return 0


def main() -> None:
    spec = yaml.safe_load(MANIFEST.read_text())
    rows = build_rows(spec)
    sys.exit(render(rows))


if __name__ == "__main__":
    main()
