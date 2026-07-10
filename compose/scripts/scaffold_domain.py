#!/usr/bin/env python3
"""Idempotent domain scaffolder — reads config/domains/<name>.yaml and generates missing artifacts.

Usage:
  just new-domain mydomain
  just scaffold-domain mydomain

Re-running with an unchanged descriptor produces zero diff (skip existing paths/files).
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

import yaml

SCRIPT_REPO = Path(__file__).resolve().parents[2]
SUPPORTED_LANGS = frozenset({"java", "python", "go", "typescript", "ruby", "dotnet"})
TEXT_SUFFIXES = {
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
    ".go",
    ".mod",
    ".sum",
    ".ts",
    ".js",
    ".mjs",
    ".cjs",
    ".rb",
    ".gemspec",
    ".cs",
    ".csproj",
    ".props",
    ".editorconfig",
}


@dataclass(frozen=True)
class ScaffoldCtx:
    root: Path

    @property
    def domains_dir(self) -> Path:
        return self.root / "config" / "domains"

    @property
    def namespaces(self) -> Path:
        return self.root / "config" / "temporal" / "namespaces.yaml"

    @property
    def tfvars(self) -> Path:
        return self.root / "deploy/terraform/layers/cloud/terraform.tfvars"

    @property
    def cluster_vars(self) -> Path:
        return self.root / "deploy/terraform/layers/cluster" / "variables.tf"

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
    if old not in text:
        die(f"scaffold anchor not found ({label}): {old!r}")
    updated = text.replace(old, new, 1)
    if updated == text:
        die(f"scaffold replace had no effect ({label})")
    return updated


def load_descriptor(ctx: ScaffoldCtx, domain: str) -> dict:
    path = ctx.domains_dir / f"{domain}.yaml"
    if not path.is_file():
        die(
            f"missing {path.relative_to(ctx.root)} - run `just new-domain {domain}` first"
        )
    descriptor = yaml.safe_load(path.read_text()) or {}
    desc_domain = str(descriptor.get("domain") or domain)
    if desc_domain != domain:
        die(
            f"{path.relative_to(ctx.root)}: domain: {desc_domain!r} must match filename {domain!r}"
        )
    return descriptor


def copy_tree_idempotent(
    src: Path, dst: Path, mapping: dict[str, str], *, force: bool = False
) -> bool:
    """Copy template tree; skip paths that already exist unless force. Returns True if anything written."""
    if not src.is_dir():
        die(f"missing template tree: {src}")
    wrote = False
    for path in sorted(src.rglob("*")):
        rel = path.relative_to(src)
        rel_str = substitute(str(rel), mapping)
        out = dst / rel_str
        if path.is_dir():
            if not out.exists():
                out.mkdir(parents=True, exist_ok=True)
                wrote = True
            continue
        if out.exists() and not force:
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        if (
            path.suffix in TEXT_SUFFIXES
            or path.name
            in {
                "Dockerfile",
                ".npmrc",
                "Gemfile",
                "Directory.Build.props",
                "Directory.Packages.props",
                ".editorconfig",
            }
            or path.name.endswith(".gemspec")
        ):
            out.write_text(substitute(path.read_text(), mapping))
        else:
            shutil.copy2(path, out)
        wrote = True
    return wrote


def write_if_changed(path: Path, content: str) -> bool:
    if path.is_file() and path.read_text() == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return True


def template_root_for_lang(lang: str) -> Path:
    root = SCRIPT_REPO / "templates/domain" / lang
    if not root.is_dir():
        die(f"missing template tree templates/domain/{lang}/")
    return root


def lib_template_rel(lang: str) -> str:
    if lang == "python":
        return "libs/{{DOMAIN}}/python"
    if lang == "java":
        return "libs/{{DOMAIN}}/java"
    if lang == "typescript":
        return "libs/{{DOMAIN}}/typescript"
    if lang == "ruby":
        return "libs/{{DOMAIN}}/ruby"
    die(f"unsupported language {lang!r} for lib scaffold")
    return ""


def worker_template_rel(lang: str, profile: str) -> str:
    return f"apps/temporal/workers/{lang}/{{{{DOMAIN}}}}/{profile}"


def languages_for_descriptor(descriptor: dict) -> set[str]:
    langs: set[str] = set()
    for worker in descriptor.get("workers") or []:
        if language := worker.get("language"):
            langs.add(str(language).lower())
    return langs


def scaffold_libs(
    ctx: ScaffoldCtx,
    domain: str,
    languages: set[str],
    mapping: dict[str, str],
    force: bool,
) -> None:
    for lang in sorted(languages):
        if lang in {"go", "dotnet"}:
            continue
        if lang not in SUPPORTED_LANGS:
            die(f"unsupported language {lang!r} in descriptor")
        template_rel = lib_template_rel(lang)
        src = template_root_for_lang(lang) / template_rel
        dst = ctx.root / substitute(template_rel, mapping)
        if src.is_dir():
            copy_tree_idempotent(src, dst, mapping, force=force)


def scaffold_dotnet_domain_root(
    ctx: ScaffoldCtx, domain: str, mapping: dict[str, str], force: bool
) -> None:
    """Materialize shared MSBuild props once per dotnet domain worker tree."""
    template_rel = "apps/temporal/workers/dotnet/{{DOMAIN}}"
    src_root = template_root_for_lang("dotnet") / template_rel
    dst_root = ctx.root / substitute(template_rel, mapping)
    for name in (
        "Directory.Build.props",
        "Directory.Packages.props",
        ".editorconfig",
        "CamelCasePayloadConverter.cs",
        "TemporalIds.cs",
    ):
        src = src_root / name
        if not src.is_file():
            continue
        out = dst_root / name
        if out.exists() and not force:
            continue
        dst_root.mkdir(parents=True, exist_ok=True)
        out.write_text(substitute(src.read_text(), mapping))


def scaffold_workers(
    ctx: ScaffoldCtx,
    descriptor: dict,
    domain: str,
    mapping: dict[str, str],
    force: bool,
) -> None:
    for worker in descriptor.get("workers") or []:
        profile = str(worker.get("profile") or "")
        language = str(worker.get("language") or "").lower()
        if not profile or not language:
            die(f"workers[] entry in {domain}.yaml requires profile and language")
        if language not in SUPPORTED_LANGS:
            die(f"unsupported worker language {language!r}")
        template_rel = worker_template_rel(language, profile)
        src = template_root_for_lang(language) / template_rel
        dst = ctx.root / substitute(template_rel, mapping)
        if not src.is_dir():
            die(
                f"missing worker template {template_rel} for language {language!r} - "
                f"add templates/domain/{language}/.../{profile}/"
            )
        copy_tree_idempotent(src, dst, mapping, force=force)


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
        return
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
    ctx.cluster_vars.write_text(text.rstrip() + block)


def patch_pyproject(ctx: ScaffoldCtx, domain: str, descriptor: dict) -> None:
    text = ctx.pyproject.read_text()
    member = f'"libs/{domain}/python"'
    if member not in text:
        match = re.search(r"^members = \[(.*)\]$", text, flags=re.MULTILINE)
        if not match:
            die("could not find [tool.uv.workspace] members in pyproject.toml")
        inner = match.group(1).strip()
        new_members = (
            f"members = [{member}, {inner}]" if inner else f"members = [{member}]"
        )
        text = require_replace(
            text,
            match.group(0),
            new_members,
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
        match = re.search(r"^default-groups = \[(.*)\]$", text, flags=re.MULTILINE)
        if not match:
            die("could not find [tool.uv] default-groups in pyproject.toml")
        inner = match.group(1).strip()
        new_groups = (
            f'default-groups = ["{group}", {inner}]'
            if inner
            else f'default-groups = ["{group}"]'
        )
        text = require_replace(
            text,
            match.group(0),
            new_groups,
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
    for worker in descriptor.get("workers") or []:
        if str(worker.get("language", "")).lower() != "python":
            continue
        profile = str(worker.get("profile") or "")
        if profile not in {"workflow", "activity"}:
            continue
        pyright_root = f'{{ root = "apps/temporal/workers/python/{domain}/{profile}" }}'
        if pyright_root not in text:
            anchor = f'{{ root = "apps/temporal/workers/python/orders/{profile}" }}'
            text = require_replace(
                text,
                anchor,
                pyright_root + ",\n  " + anchor,
                label=f"pyproject pyright {profile} root",
            )
    ctx.pyproject.write_text(text)


def patch_settings_gradle(ctx: ScaffoldCtx, domain: str, descriptor: dict) -> None:
    path = ctx.settings_gradle
    if not path.is_file():
        die(
            "missing settings.gradle - add the Gradle spine before scaffolding Java workers"
        )
    text = path.read_text()
    lib_include = f"include '{domain}-lib'"
    if lib_include in text:
        return
    anchor = "project(':appkit-java').projectDir = file('libs/appkit/java')"
    blocks = [
        "",
        f"include '{domain}-lib'",
        f"project(':{domain}-lib').projectDir = file('libs/{domain}/java')",
        "",
    ]
    for worker in descriptor.get("workers") or []:
        if str(worker.get("language", "")).lower() != "java":
            continue
        profile = str(worker["profile"])
        module = f"{domain}-{profile}-worker"
        rel = f"apps/temporal/workers/java/{domain}/{profile}"
        blocks.extend(
            [
                f"include '{module}'",
                f"project(':{module}').projectDir = file('{rel}')",
                "",
            ]
        )
    block = "\n".join(blocks).rstrip()
    text = require_replace(text, anchor, anchor + "\n" + block, label="settings.gradle")
    path.write_text(text)


def finalize_go_modules(ctx: ScaffoldCtx, descriptor: dict, domain: str) -> None:
    """Generate go.sum for each Go worker module (images/go.Dockerfile requires it)."""
    for worker in descriptor.get("workers") or []:
        if str(worker.get("language", "")).lower() != "go":
            continue
        profile = str(worker.get("profile") or "")
        mod_dir = ctx.root / f"apps/temporal/workers/go/{domain}/{profile}"
        if not (mod_dir / "go.mod").is_file():
            continue
        subprocess.run(["go", "mod", "tidy"], cwd=mod_dir, check=True)


def bundle_lock_dir(ctx: ScaffoldCtx, bundle_dir: Path) -> None:
    """Run bundle lock with Ruby 3.3+ (prefer host when new enough, else Docker)."""
    rel = bundle_dir.relative_to(ctx.root).as_posix()

    host_ruby = subprocess.run(
        [
            "ruby",
            "-e",
            "exit(Gem::Version.new(RUBY_VERSION) >= Gem::Version.new('3.3') ? 0 : 1)",
        ],
        cwd=bundle_dir,
        check=False,
    )
    if host_ruby.returncode == 0:
        subprocess.run(["bundle", "lock"], cwd=bundle_dir, check=True)
        return

    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{ctx.root}:/repo",
            "-w",
            f"/repo/{rel}",
            "ruby:3.3-slim",
            "bundle",
            "lock",
        ],
        check=True,
    )


def finalize_ruby_bundles(ctx: ScaffoldCtx, descriptor: dict, domain: str) -> None:
    """Generate Gemfile.lock for each Ruby lib/worker (images/ruby.Dockerfile requires it)."""
    dirs: list[Path] = []
    lib_dir = ctx.root / f"libs/{domain}/ruby"
    if lib_dir.is_dir() and (lib_dir / "Gemfile").is_file():
        dirs.append(lib_dir)
    for worker in descriptor.get("workers") or []:
        if str(worker.get("language", "")).lower() != "ruby":
            continue
        profile = str(worker.get("profile") or "")
        worker_dir = ctx.root / f"apps/temporal/workers/ruby/{domain}/{profile}"
        if (worker_dir / "Gemfile").is_file():
            dirs.append(worker_dir)
    for bundle_dir in dirs:
        if (bundle_dir / "Gemfile.lock").is_file():
            continue
        bundle_lock_dir(ctx, bundle_dir)


def patch_go_workspace(ctx: ScaffoldCtx, domain: str, descriptor: dict) -> None:
    """Ensure root go.work lists Go worker modules when present (optional)."""
    go_work = ctx.root / "go.work"
    if not go_work.is_file():
        return
    text = go_work.read_text()
    module_lines = [
        f"\t./apps/temporal/workers/go/{domain}/{str(worker['profile'])}"
        for worker in descriptor.get("workers") or []
        if str(worker.get("language", "")).lower() == "go" and worker.get("profile")
    ]
    if not module_lines:
        return
    if "use (" not in text:
        return
    changed = False
    for module_line in module_lines:
        if module_line in text:
            continue
        text = require_replace(
            text,
            "use (",
            "use (\n" + module_line,
            label="go.work use block",
        )
        changed = True
    if changed:
        go_work.write_text(text)


def patch_typescript_workspace(ctx: ScaffoldCtx, domain: str) -> None:
    root_pkg = ctx.root / "package.json"
    if not root_pkg.is_file():
        write_if_changed(
            root_pkg,
            '{\n  "name": "temporal-demo-node",\n  "private": true,\n  "type": "module"\n}\n',
        )
    workspace = ctx.root / "pnpm-workspace.yaml"
    if not workspace.is_file():
        pkg = textwrap.dedent(
            f"""\
            packages:
              - "libs/{domain}/typescript"
              - "apps/temporal/workers/typescript/{domain}/*"
            """
        )
        write_if_changed(workspace, pkg)
        return
    text = workspace.read_text()
    entries = [
        f'  - "libs/{domain}/typescript"',
        f'  - "apps/temporal/workers/typescript/{domain}/*"',
    ]
    changed = False
    for entry in entries:
        if entry.strip("- ") not in text:
            text = text.rstrip() + "\n" + entry + "\n"
            changed = True
    if changed:
        workspace.write_text(text)


def chart_workers_values(descriptor: dict) -> list[dict]:
    domain = str(descriptor["domain"])
    values: list[dict] = []
    for worker in descriptor.get("workers") or []:
        profile = str(worker["profile"])
        language = str(worker.get("language") or "python").lower()
        kind = str(worker.get("kind") or "activity")
        entry: dict = {
            "name": profile,
            "deploymentName": worker["deployment_name"],
            "taskQueue": worker["task_queue"],
            "kind": kind,
            "replicas": worker.get("replicas", 2 if kind == "activity" else 1),
            "image": {
                "repository": f"localhost:5001/{domain}-worker-{profile}",
                "tag": "latest",
            },
        }
        if language == "python":
            entry["command"] = ["python", "main.py"]
        elif language == "ruby":
            entry["command"] = ["ruby", "worker.rb"]
        elif language == "dotnet":
            entry["command"] = ["dotnet", "Worker.dll"]
        if autoscaling := worker.get("autoscaling"):
            entry["autoscaling"] = autoscaling
        values.append(entry)
    return values


def sync_chart_values(
    ctx: ScaffoldCtx, descriptor: dict, mapping: dict[str, str]
) -> None:
    values_path = (
        ctx.root / "deploy/charts" / f"{descriptor['domain']}-workers/values.yaml"
    )
    if not values_path.is_file():
        return
    text = values_path.read_text()
    data = yaml.safe_load(text) or {}
    data["workers"] = chart_workers_values(descriptor)
    data.pop("autoscaling", None)
    rendered = yaml.safe_dump(data, sort_keys=False)
    write_if_changed(values_path, rendered)


def scaffold_chart(
    ctx: ScaffoldCtx,
    domain: str,
    descriptor: dict,
    mapping: dict[str, str],
    force: bool,
) -> None:
    dst = ctx.root / "deploy/charts" / f"{domain}-workers"
    if dst.exists() and not force:
        sync_chart_values(ctx, descriptor, mapping)
        return
    if dst.exists() and force:
        shutil.rmtree(dst)
    copy_tree_idempotent(ctx.chart_template, dst, mapping, force=True)
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
    for worker in descriptor.get("workers") or []:
        if str(worker.get("language", "")).lower() == "java":
            values = dst / "values.yaml"
            values.write_text(
                values.read_text().replace('    command: ["python", "main.py"]\n', "")
            )
    sync_chart_values(ctx, descriptor, mapping)


def scaffold_grafana(ctx: ScaffoldCtx, domain: str, mapping: dict[str, str]) -> None:
    dash_dir = ctx.root / "compose/observability/grafana/dashboards" / domain
    dash_path = dash_dir / f"{domain}.json"
    if dash_path.is_file():
        return
    dash_dir.mkdir(parents=True, exist_ok=True)
    dash_path.write_text(
        substitute(ctx.grafana_dashboard_template.read_text(), mapping)
    )


def patch_language_manifests(ctx: ScaffoldCtx, domain: str, descriptor: dict) -> None:
    languages = languages_for_descriptor(descriptor)
    if "python" in languages:
        patch_pyproject(ctx, domain, descriptor)
    if "java" in languages:
        patch_settings_gradle(ctx, domain, descriptor)
    if "go" in languages:
        patch_go_workspace(ctx, domain, descriptor)
        finalize_go_modules(ctx, descriptor, domain)
    if "typescript" in languages:
        patch_typescript_workspace(ctx, domain)
    if "ruby" in languages:
        finalize_ruby_bundles(ctx, descriptor, domain)


def scaffold_domain(ctx: ScaffoldCtx, domain: str, *, force: bool = False) -> None:
    descriptor = load_descriptor(ctx, domain)
    mapping = tokens(domain)
    languages = languages_for_descriptor(descriptor)

    append_namespace(ctx, domain)
    append_cloud_overlay(ctx, domain)
    add_chart_version_variable(ctx, domain)

    scaffold_libs(ctx, domain, languages, mapping, force)
    if "dotnet" in languages:
        scaffold_dotnet_domain_root(ctx, domain, mapping, force)
    scaffold_workers(ctx, descriptor, domain, mapping, force)
    scaffold_chart(ctx, domain, descriptor, mapping, force)

    if (descriptor.get("observability") or {}).get("dashboard"):
        scaffold_grafana(ctx, domain, mapping)

    patch_language_manifests(ctx, domain, descriptor)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scaffold a Temporal domain from its descriptor"
    )
    parser.add_argument(
        "--name", required=True, help="domain key (must match descriptor filename)"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite existing generated paths (non-idempotent)",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=SCRIPT_REPO,
        help="repository root to write into (default: script repo)",
    )
    args = parser.parse_args()

    domain = args.name.strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9-]*", domain):
        die("domain name must match [a-z][a-z0-9-]*")

    ctx = ScaffoldCtx(root=args.root.resolve())
    scaffold_domain(ctx, domain, force=args.force)
    print(
        f"Scaffold complete for {domain!r} (idempotent — existing files left unchanged)."
    )
    print(f"Next: just verify-domain {domain}")


if __name__ == "__main__":
    main()
