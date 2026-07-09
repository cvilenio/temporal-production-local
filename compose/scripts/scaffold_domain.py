#!/usr/bin/env python3
"""Scaffold a new Temporal domain from templates/domain/<lang>/.

Usage:
  uv run python compose/scripts/scaffold_domain.py --name hello --lang python
  just scaffold-domain NAME=hello LANG=python

Refuses to overwrite an existing domain unless --force.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

import yaml

SCRIPT_REPO = Path(__file__).resolve().parents[2]
SUPPORTED_LANGS = {"java", "python"}


@dataclass(frozen=True)
class ScaffoldCtx:
    """Target tree (--root) plus template sources (always under SCRIPT_REPO)."""

    root: Path
    template_root: Path

    @property
    def namespaces(self) -> Path:
        return self.root / "config" / "temporal" / "namespaces.yaml"

    @property
    def tfvars(self) -> Path:
        return self.root / "deploy/terraform/layers/cloud/terraform.tfvars"

    @property
    def cluster_vars(self) -> Path:
        return self.root / "deploy/terraform/layers/cluster/variables.tf"

    @property
    def pyproject(self) -> Path:
        return self.root / "pyproject.toml"

    @property
    def settings_gradle(self) -> Path:
        return self.root / "settings.gradle"

    @property
    def chart_template(self) -> Path:
        return SCRIPT_REPO / "templates/charts/domain-workers"

    @property
    def grafana_dashboard_template(self) -> Path:
        return SCRIPT_REPO / "templates/grafana/dashboard.json"

    @property
    def grafana_provisioning_template(self) -> Path:
        return SCRIPT_REPO / "templates/grafana/provisioning.yaml"


def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def tokens(domain: str, lang: str = "python") -> dict[str, str]:
    return {
        "{{DOMAIN}}": domain,
        "{{Domain}}": domain.replace("-", " ").title().replace(" ", ""),
        "{{DOMAIN_UPPER}}": domain.upper().replace("-", "_"),
        "{{DOMAIN_PKG}}": domain.replace("-", ""),
        "{{LANG}}": lang,
    }


def substitute(text: str, mapping: dict[str, str]) -> str:
    for key, val in mapping.items():
        text = text.replace(key, val)
    return text


def require_replace(text: str, old: str, new: str, *, label: str) -> str:
    """Replace once; die if anchor missing or replace had no effect."""
    if old not in text:
        die(f"scaffold anchor not found ({label}): {old!r}")
    updated = text.replace(old, new, 1)
    if updated == text:
        die(f"scaffold replace had no effect ({label})")
    return updated


def copy_tree(ctx: ScaffoldCtx, src: Path, dst: Path, mapping: dict[str, str]) -> None:
    if dst.exists():
        die(f"refusing to overwrite existing path: {dst.relative_to(ctx.root)}")
    for path in src.rglob("*"):
        rel = path.relative_to(src)
        rel_str = substitute(str(rel), mapping)
        out = dst / rel_str
        if path.is_dir():
            out.mkdir(parents=True, exist_ok=True)
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix in {
            ".py",
            ".toml",
            ".yaml",
            ".yml",
            ".json",
            ".md",
            ".txt",
            ".tf",
            ".java",
            ".gradle",
        }:
            out.write_text(substitute(path.read_text(), mapping))
        else:
            shutil.copy2(path, out)


def domain_exists(ctx: ScaffoldCtx, domain: str) -> bool:
    if (ctx.root / "config" / "domains" / f"{domain}.yaml").exists():
        return True
    ns = yaml.safe_load(ctx.namespaces.read_text()) or {}
    return domain in (ns.get("domains") or {})


def write_domain_descriptor(ctx: ScaffoldCtx, domain: str, lang: str) -> None:
    path = ctx.root / "config" / "domains" / f"{domain}.yaml"
    data = {
        "domain": domain,
        "language": lang,
        "data_converter": "default",
        "workers": [
            {
                "profile": "workflow",
                "kind": "workflow",
                "deployment_name": f"{domain}-workflow-{lang}",
                "task_queue": f"{domain}-workflow-task-queue",
            },
            {
                "profile": "activity",
                "kind": "activity",
                "deployment_name": f"{domain}-activity-{lang}",
                "task_queue": f"{domain}-activity-task-queue",
            },
        ],
        "workflows": [
            {
                "type": "HelloWorkflow",
                "task_queue": f"{domain}-workflow-task-queue",
                "sample_inputs": [
                    {"label": "happy_path", "input": {"name": "Temporal"}},
                ],
            }
        ],
        "observability": {"dashboard": True},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def append_namespace(ctx: ScaffoldCtx, domain: str) -> None:
    text = ctx.namespaces.read_text()
    if re.search(rf"^\s{{2}}{re.escape(domain)}:", text, flags=re.MULTILINE):
        return
    insertion = f"  {domain}:\n    retention_days: 30\n    search_attributes: {{}}\n"
    updated, n = re.subn(
        r"^domains:\n",
        f"domains:\n{insertion}",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if n != 1:
        die("could not find domains: block in namespaces.yaml")
    ctx.namespaces.write_text(updated)


def append_cloud_overlay(ctx: ScaffoldCtx, domain: str) -> None:
    if not ctx.tfvars.exists():
        die(
            f"missing {ctx.tfvars.relative_to(ctx.root)} — copy from terraform.tfvars.example"
        )
    text = ctx.tfvars.read_text()
    if f'"{domain}"' in text:
        return
    entry = textwrap.dedent(
        f"""
          "{domain}" = {{
            service_account_name = "{domain}-workers"
            api_key_display_name = "{domain}-workers-key"
            api_key_expiry_time  = "2027-06-23T00:00:00Z"
          }}
        """
    ).rstrip()
    text = text.rstrip()
    if text.endswith("}"):
        text = text[:-1].rstrip() + ",\n" + entry + "\n}\n"
    else:
        die("cloud_overlay block not found in terraform.tfvars")
    ctx.tfvars.write_text(text)


def add_chart_version_variable(ctx: ScaffoldCtx, domain: str) -> None:
    var_name = f"{domain.replace('-', '_')}_workers_chart_version"
    text = ctx.cluster_vars.read_text()
    if var_name in text:
        return
    block = textwrap.dedent(
        f"""

        variable "{var_name}" {{
          description = "Published version of the {domain}-workers OCI chart."
          type        = string
          default     = "0.1.0"
        }}
        """
    )
    text = text.rstrip() + block
    ctx.cluster_vars.write_text(text)


def patch_pyproject(ctx: ScaffoldCtx, domain: str) -> None:
    text = ctx.pyproject.read_text()
    member = f'"libs/{domain}/python"'
    if member not in text:
        text = require_replace(
            text,
            'members = ["libs/orders/python"',
            f'members = ["libs/{domain}/python", "libs/orders/python"',
            label="pyproject workspace members",
        )
    group = f"{domain}-workers"
    if f"{group} = [" not in text:
        insert = f'{group} = ["{domain}", "appkit", "dependency-injector>=4.49"]\n'
        text = require_replace(
            text,
            "workers = [",
            insert + "workers = [",
            label="pyproject dependency group",
        )
    if group not in text.split("default-groups")[1]:
        text = require_replace(
            text,
            'default-groups = ["workers"',
            f'default-groups = ["{group}", "workers"',
            label="pyproject default-groups",
        )
    source_key = f"{domain} = {{ workspace = true }}"
    if source_key not in text:
        text = require_replace(
            text,
            "orders = { workspace = true }",
            f"{source_key}\norders = {{ workspace = true }}",
            label="pyproject workspace sources",
        )
    pyright_root = f'{{ root = "apps/temporal/workers/python/{domain}/workflow" }}'
    if pyright_root not in text:
        text = require_replace(
            text,
            '{ root = "apps/temporal/workers/python/workflow" }',
            pyright_root + ',\n  { root = "apps/temporal/workers/python/workflow" }',
            label="pyproject pyright workflow root",
        )
        text = require_replace(
            text,
            '{ root = "apps/temporal/workers/python/activity" }',
            f'{{ root = "apps/temporal/workers/python/{domain}/activity" }},\n  {{ root = "apps/temporal/workers/python/activity" }}',
            label="pyproject pyright activity root",
        )
    ctx.pyproject.write_text(text)


def patch_settings_gradle(ctx: ScaffoldCtx, domain: str) -> None:
    path = ctx.settings_gradle
    if not path.is_file():
        die(
            "missing settings.gradle — add the M5 Gradle spine before scaffolding Java domains"
        )
    text = path.read_text()
    lib_include = f"include '{domain}-lib'"
    if lib_include in text:
        return
    anchor = "project(':appkit-java').projectDir = file('libs/appkit/java')"
    block = textwrap.dedent(
        f"""

        include '{domain}-lib'
        project(':{domain}-lib').projectDir = file('libs/{domain}/java')

        include '{domain}-workflow-worker'
        project(':{domain}-workflow-worker').projectDir = file('apps/temporal/workers/java/{domain}/workflow')

        include '{domain}-activity-worker'
        project(':{domain}-activity-worker').projectDir = file('apps/temporal/workers/java/{domain}/activity')
        """
    ).rstrip()
    text = require_replace(
        text, anchor, anchor + block, label="settings.gradle domain modules"
    )
    path.write_text(text)


def patch_chart_for_lang(ctx: ScaffoldCtx, domain: str, lang: str) -> None:
    """Java images use the Dockerfile ENTRYPOINT (JAVA_OPTS + jar); omit chart command."""
    if lang != "java":
        return
    values = ctx.root / "deploy/charts" / f"{domain}-workers" / "values.yaml"
    text = values.read_text()
    text = text.replace('    command: ["python", "main.py"]\n', "")
    values.write_text(text)


def domain_copy_paths(lang: str, domain: str) -> list[tuple[str, str]]:
    """Return (template_rel, dest_rel) pairs for the domain tree."""
    if lang == "python":
        return [
            ("libs/{{DOMAIN}}/python", f"libs/{domain}/python"),
            (
                "apps/temporal/workers/python/{{DOMAIN}}",
                f"apps/temporal/workers/python/{domain}",
            ),
        ]
    if lang == "java":
        return [
            ("libs/{{DOMAIN}}/java", f"libs/{domain}/java"),
            (
                "apps/temporal/workers/java/{{DOMAIN}}",
                f"apps/temporal/workers/java/{domain}",
            ),
        ]
    die(f"unsupported language {lang!r}")


def scaffold_chart(ctx: ScaffoldCtx, domain: str, mapping: dict[str, str]) -> None:
    dst = ctx.root / "deploy/charts" / f"{domain}-workers"
    copy_tree(ctx, ctx.chart_template, dst, mapping)
    for path in dst.rglob("*"):
        if path.is_file() and path.suffix in {".yaml", ".yml"}:
            path.write_text(substitute(path.read_text(), mapping))
    chart_yaml = dst / "Chart.yaml"
    chart_text = chart_yaml.read_text()
    chart_text = re.sub(
        r"^name: orders-workers",
        f"name: {domain}-workers",
        chart_text,
        flags=re.MULTILINE,
    )
    chart_text = re.sub(
        r"^version: .*",
        "version: 0.1.0",
        chart_text,
        flags=re.MULTILINE,
    )
    chart_text = re.sub(
        r"^# Keep in lockstep with cluster TF orders_workers_chart_version\.",
        f"# Keep in lockstep with cluster TF {domain.replace('-', '_')}_workers_chart_version.",
        chart_text,
        flags=re.MULTILINE,
    )
    chart_yaml.write_text(chart_text)


def scaffold_grafana(ctx: ScaffoldCtx, domain: str, mapping: dict[str, str]) -> None:
    dash_dir = ctx.root / "compose/observability/grafana/dashboards" / domain
    dash_dir.mkdir(parents=True, exist_ok=True)
    dash_path = dash_dir / f"{domain}.json"
    dash_path.write_text(
        substitute(ctx.grafana_dashboard_template.read_text(), mapping)
    )
    prov_dir = ctx.root / "compose/observability/grafana/provisioning/dashboards"
    prov_dir.mkdir(parents=True, exist_ok=True)
    prov_path = prov_dir / f"{domain}.yaml"
    if prov_path.exists():
        die(f"grafana provisioning already exists: {prov_path.relative_to(ctx.root)}")
    prov_path.write_text(
        substitute(ctx.grafana_provisioning_template.read_text(), mapping)
    )


def print_next_steps(domain: str, lang: str) -> None:
    chart_var = domain.replace("-", "_")
    if lang == "java":
        build_block = textwrap.dedent(
            f"""
              TAG=$(git describe --always --dirty)
              just java-build
              docker build -f images/java.Dockerfile \\
                --build-arg DOMAIN={domain} \\
                --build-arg APP_MODULE=:{domain}-workflow-worker \\
                --build-arg WORKER_REL_PATH=apps/temporal/workers/java/{domain}/workflow \\
                --build-arg APP_JAR={domain}-workflow-worker \\
                -t localhost:5001/{domain}-worker-workflow:$TAG .
              docker build -f images/java.Dockerfile \\
                --build-arg DOMAIN={domain} \\
                --build-arg APP_MODULE=:{domain}-activity-worker \\
                --build-arg WORKER_REL_PATH=apps/temporal/workers/java/{domain}/activity \\
                --build-arg APP_JAR={domain}-activity-worker \\
                -t localhost:5001/{domain}-worker-activity:$TAG .
              docker push localhost:5001/{domain}-worker-workflow:$TAG
              docker push localhost:5001/{domain}-worker-activity:$TAG
            """
        ).rstrip()
        extra = ""
    else:
        build_block = textwrap.dedent(
            f"""
              TAG=$(git describe --always --dirty)
              docker build -f images/python.Dockerfile \\
                --build-arg APP_GROUP={domain}-workers \\
                --build-arg APP_PATH=apps/temporal/workers/python/{domain}/workflow \\
                --build-arg APP_CMD=python --build-arg APP_MODULE=main \\
                -t localhost:5001/{domain}-worker-workflow:$TAG .
              docker build -f images/python.Dockerfile \\
                --build-arg APP_GROUP={domain}-workers \\
                --build-arg APP_PATH=apps/temporal/workers/python/{domain}/activity \\
                --build-arg APP_CMD=python --build-arg APP_MODULE=main \\
                -t localhost:5001/{domain}-worker-activity:$TAG .
              docker push localhost:5001/{domain}-worker-workflow:$TAG
              docker push localhost:5001/{domain}-worker-activity:$TAG
            """
        ).rstrip()
        extra = "              3. Run `uv lock` after pyproject.toml changes.\n"

    print(
        textwrap.dedent(
            f"""
            === Scaffold complete: {domain} ({lang}) ===

            Manual follow-ups (not auto-generated):
              1. Add an ArgoCD Application for {domain}-workers in
                 deploy/terraform/layers/cluster/applications.tf (copy orders_workers_application).
              2. Add Grafana volume mounts for {domain} in docker-compose.yml (copy ziggymart block).
            {extra}
            Build + deploy (run each step separately — never chain publish && apply):
            {build_block}
              just chart-publish   # confirm chart landed before apply
              # terraform apply with TF_VAR_{chart_var}_workers_chart_version=0.1.0
              # and worker image digests for {domain}

            Verify: DescribeTaskQueue on {domain}-workflow-task-queue and
            {domain}-activity-task-queue; start ONE HelloWorkflow.

            See docs/adapting-a-demo.md for the full human runbook.
            """
        ).strip()
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Scaffold a Temporal domain")
    parser.add_argument("--name", required=True, help="domain key (e.g. hello)")
    parser.add_argument("--lang", default="python", choices=sorted(SUPPORTED_LANGS))
    parser.add_argument(
        "--force", action="store_true", help="overwrite existing domain"
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=SCRIPT_REPO,
        help="repository root to write into (default: script repo)",
    )
    parser.add_argument(
        "--template-root",
        type=Path,
        default=None,
        help="domain template tree (default: <script-repo>/templates/domain/python)",
    )
    args = parser.parse_args()

    root = args.root.resolve()
    lang = args.lang
    default_template = SCRIPT_REPO / f"templates/domain/{lang}"
    template_root = (args.template_root or default_template).resolve()
    ctx = ScaffoldCtx(root=root, template_root=template_root)

    domain = args.name.strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9-]*", domain):
        die("domain name must match [a-z][a-z0-9-]*")

    if domain_exists(ctx, domain) and not args.force:
        die(f"domain {domain!r} already exists — pass --force to overwrite")

    if lang not in SUPPORTED_LANGS:
        die(
            f"language {lang!r} not supported yet (available: {sorted(SUPPORTED_LANGS)})"
        )

    if not template_root.is_dir():
        die(f"missing template tree: {template_root}")

    mapping = tokens(domain, lang)

    for template_rel, dest_rel in domain_copy_paths(lang, domain):
        src = template_root / template_rel
        dst = root / dest_rel
        if dst.exists() and args.force:
            shutil.rmtree(dst)
        copy_tree(ctx, src, dst, mapping)

    write_domain_descriptor(ctx, domain, lang)
    append_namespace(ctx, domain)
    append_cloud_overlay(ctx, domain)
    add_chart_version_variable(ctx, domain)
    if lang == "python":
        patch_pyproject(ctx, domain)
    elif lang == "java":
        patch_settings_gradle(ctx, domain)

    chart_dst = root / "deploy/charts" / f"{domain}-workers"
    if chart_dst.exists() and args.force:
        shutil.rmtree(chart_dst)
    scaffold_chart(ctx, domain, mapping)
    patch_chart_for_lang(ctx, domain, lang)
    scaffold_grafana(ctx, domain, mapping)

    print_next_steps(domain, lang)


if __name__ == "__main__":
    main()
