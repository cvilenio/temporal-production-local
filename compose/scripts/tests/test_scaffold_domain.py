"""Offline guard for domain scaffolding — no cluster or Temporal required."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCAFFOLD = REPO_ROOT / "compose/scripts/scaffold_domain.py"
VERIFY = REPO_ROOT / "compose/scripts/verify-domains.py"
DOMAIN = "scaffoldproof"
JAVA_DOMAIN = "scaffoldproofjava"


def _seed_minimal_repo(root: Path) -> None:
    (root / "config/temporal").mkdir(parents=True)
    (root / "config/temporal/namespaces.yaml").write_text("domains:\n")
    (root / "deploy/terraform/layers/cloud").mkdir(parents=True)
    (root / "deploy/terraform/layers/cloud/terraform.tfvars").write_text(
        "cloud_overlay = {\n}\n"
    )
    (root / "deploy/terraform/layers/cluster").mkdir(parents=True)
    (root / "deploy/terraform/layers/cluster/variables.tf").write_text(
        'variable "orders_workers_chart_version" {\n  default = "0.0.0"\n}\n'
    )
    (root / "pyproject.toml").write_text((REPO_ROOT / "pyproject.toml").read_text())


def _seed_minimal_java_repo(root: Path) -> None:
    _seed_minimal_repo(root)
    (root / "settings.gradle").write_text((REPO_ROOT / "settings.gradle").read_text())


def test_scaffold_domain_python_and_verify(tmp_path: Path) -> None:
    _seed_minimal_repo(tmp_path)
    template_root = REPO_ROOT / "templates/domain/python"

    subprocess.run(
        [
            sys.executable,
            str(SCAFFOLD),
            "--name",
            DOMAIN,
            "--lang",
            "python",
            "--root",
            str(tmp_path),
            "--template-root",
            str(template_root),
        ],
        check=True,
        cwd=REPO_ROOT,
    )

    expected_files = [
        f"config/domains/{DOMAIN}.yaml",
        f"libs/{DOMAIN}/python/{DOMAIN}/shared/temporal_ids.py",
        f"libs/{DOMAIN}/python/{DOMAIN}/workflows/hello_workflow.py",
        f"apps/temporal/workers/python/{DOMAIN}/workflow/main.py",
        f"apps/temporal/workers/python/{DOMAIN}/activity/main.py",
        f"deploy/charts/{DOMAIN}-workers/Chart.yaml",
        f"compose/observability/grafana/dashboards/{DOMAIN}/{DOMAIN}.json",
        f"compose/observability/grafana/provisioning/dashboards/{DOMAIN}.yaml",
    ]
    for rel in expected_files:
        path = tmp_path / rel
        assert path.is_file(), f"missing scaffolded file: {rel}"

    dash = (
        tmp_path / f"compose/observability/grafana/dashboards/{DOMAIN}/{DOMAIN}.json"
    ).read_text()
    assert "prometheus-kind" in dash
    assert f'namespace=\\"{DOMAIN}\\"' in dash

    env = {**os.environ, "DOMAIN_VERIFY_ROOT": str(tmp_path)}
    verify = subprocess.run(
        [sys.executable, str(VERIFY)],
        env=env,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    assert verify.returncode == 0, verify.stdout + verify.stderr
    assert DOMAIN in verify.stdout


def test_scaffold_domain_java_and_verify(tmp_path: Path) -> None:
    _seed_minimal_java_repo(tmp_path)
    template_root = REPO_ROOT / "templates/domain/java"

    subprocess.run(
        [
            sys.executable,
            str(SCAFFOLD),
            "--name",
            JAVA_DOMAIN,
            "--lang",
            "java",
            "--root",
            str(tmp_path),
            "--template-root",
            str(template_root),
        ],
        check=True,
        cwd=REPO_ROOT,
    )

    expected_files = [
        f"config/domains/{JAVA_DOMAIN}.yaml",
        f"libs/{JAVA_DOMAIN}/java/src/main/java/io/temporal/demo/scaffoldproofjava/shared/TemporalIds.java",
        f"apps/temporal/workers/java/{JAVA_DOMAIN}/workflow/src/main/java/io/temporal/demo/scaffoldproofjava/workflow/HelloWorkflowImpl.java",
        f"apps/temporal/workers/java/{JAVA_DOMAIN}/workflow/src/main/resources/application.yml",
        f"apps/temporal/workers/java/{JAVA_DOMAIN}/activity/src/main/resources/application.yml",
        f"deploy/charts/{JAVA_DOMAIN}-workers/Chart.yaml",
    ]
    for rel in expected_files:
        path = tmp_path / rel
        assert path.is_file(), f"missing scaffolded file: {rel}"

    values = (tmp_path / f"deploy/charts/{JAVA_DOMAIN}-workers/values.yaml").read_text()
    assert 'command: ["java"' not in values
    assert 'command: ["python"' not in values

    settings = (tmp_path / "settings.gradle").read_text()
    assert f"include '{JAVA_DOMAIN}-lib'" in settings
    assert f"include '{JAVA_DOMAIN}-workflow-worker'" in settings

    impl = (
        tmp_path
        / f"apps/temporal/workers/java/{JAVA_DOMAIN}/workflow/src/main/java/io/temporal/demo/scaffoldproofjava/workflow/HelloWorkflowImpl.java"
    ).read_text()
    assert "setTaskQueue(TemporalIds.ACTIVITY_TASK_QUEUE)" in impl

    env = {**os.environ, "DOMAIN_VERIFY_ROOT": str(tmp_path)}
    verify = subprocess.run(
        [sys.executable, str(VERIFY)],
        env=env,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    assert verify.returncode == 0, verify.stdout + verify.stderr
    assert JAVA_DOMAIN in verify.stdout
