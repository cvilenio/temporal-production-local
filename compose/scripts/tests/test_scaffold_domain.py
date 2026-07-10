"""Offline guard for domain scaffolding — no cluster or Temporal required."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
SCAFFOLD = REPO_ROOT / "compose/scripts/scaffold_domain.py"
NEW_DOMAIN = REPO_ROOT / "compose/scripts/new_domain.py"
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
    (root / "images").mkdir(parents=True)
    for lang in ("python", "java", "go", "typescript"):
        dockerfile = REPO_ROOT / f"images/{lang}.Dockerfile"
        if dockerfile.is_file():
            shutil.copy(dockerfile, root / f"images/{lang}.Dockerfile")


def _seed_minimal_java_repo(root: Path) -> None:
    _seed_minimal_repo(root)
    (root / "settings.gradle").write_text((REPO_ROOT / "settings.gradle").read_text())


def _write_python_descriptor(root: Path, domain: str) -> None:
    subprocess.run(
        [sys.executable, str(NEW_DOMAIN), "--name", domain, "--root", str(root)],
        check=True,
        cwd=REPO_ROOT,
    )


def _write_java_descriptor(root: Path, domain: str) -> None:
    path = root / f"config/domains/{domain}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {
                "domain": domain,
                "k8s_namespace": "orders",
                "data_converter": "default",
                "workers": [
                    {
                        "profile": "workflow",
                        "language": "java",
                        "kind": "workflow",
                        "deployment_name": f"{domain}-workflow-java",
                        "task_queue": f"{domain}-workflow-task-queue",
                    },
                    {
                        "profile": "activity",
                        "language": "java",
                        "kind": "activity",
                        "deployment_name": f"{domain}-activity-java",
                        "task_queue": f"{domain}-activity-task-queue",
                    },
                ],
                "workflows": [
                    {
                        "type": "HelloWorkflow",
                        "task_queue": f"{domain}-workflow-task-queue",
                        "sample_inputs": [
                            {"label": "happy_path", "input": {"name": "Temporal"}}
                        ],
                    }
                ],
                "observability": {"dashboard": True},
            },
            sort_keys=False,
        )
    )


def test_scaffold_domain_python_idempotent(tmp_path: Path) -> None:
    _seed_minimal_repo(tmp_path)
    _write_python_descriptor(tmp_path, DOMAIN)

    cmd = [
        sys.executable,
        str(SCAFFOLD),
        "--name",
        DOMAIN,
        "--root",
        str(tmp_path),
    ]
    subprocess.run(cmd, check=True, cwd=REPO_ROOT)
    subprocess.run(cmd, check=True, cwd=REPO_ROOT)

    expected_files = [
        f"config/domains/{DOMAIN}.yaml",
        f"libs/{DOMAIN}/python/{DOMAIN}/shared/temporal_ids.py",
        f"libs/{DOMAIN}/python/{DOMAIN}/workflows/hello_workflow.py",
        f"apps/temporal/workers/python/{DOMAIN}/workflow/main.py",
        f"apps/temporal/workers/python/{DOMAIN}/activity/main.py",
        f"deploy/charts/{DOMAIN}-workers/Chart.yaml",
        f"compose/observability/grafana/dashboards/{DOMAIN}/{DOMAIN}.json",
    ]
    for rel in expected_files:
        assert (tmp_path / rel).is_file(), f"missing scaffolded file: {rel}"

    dash = (
        tmp_path / f"compose/observability/grafana/dashboards/{DOMAIN}/{DOMAIN}.json"
    ).read_text()
    assert "prometheus-kind" in dash

    env = {**os.environ, "DOMAIN_VERIFY_ROOT": str(tmp_path)}
    verify = subprocess.run(
        [sys.executable, str(VERIFY), DOMAIN],
        env=env,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    assert verify.returncode == 0, verify.stdout + verify.stderr

    values_path = tmp_path / f"deploy/charts/{DOMAIN}-workers/values.yaml"
    values = yaml.safe_load(values_path.read_text())
    assert "autoscaling" not in values
    for worker in values["workers"]:
        assert "taskQueue" in worker
        assert "kind" in worker
        assert "autoscaling" not in worker


def test_scaffold_chart_values_passes_descriptor_autoscaling(tmp_path: Path) -> None:
    domain = "scaffoldautos"
    _seed_minimal_repo(tmp_path)
    desc_path = tmp_path / f"config/domains/{domain}.yaml"
    desc_path.parent.mkdir(parents=True, exist_ok=True)
    desc_path.write_text(
        yaml.safe_dump(
            {
                "domain": domain,
                "k8s_namespace": "orders",
                "data_converter": "default",
                "workers": [
                    {
                        "profile": "workflow",
                        "language": "python",
                        "kind": "workflow",
                        "deployment_name": f"{domain}-workflow-python",
                        "task_queue": f"{domain}-workflow-task-queue",
                        "autoscaling": {
                            "minReplicas": 2,
                            "maxReplicas": 8,
                            "targetBacklogPerReplica": 3,
                            "slotScaleUpEnabled": True,
                        },
                    },
                    {
                        "profile": "activity",
                        "language": "python",
                        "kind": "activity",
                        "deployment_name": f"{domain}-activity-python",
                        "task_queue": f"{domain}-activity-task-queue",
                        "autoscaling": {
                            "minReplicas": 1,
                            "maxReplicas": 12,
                            "targetBacklogPerReplica": 7,
                        },
                    },
                ],
                "workflows": [
                    {
                        "type": "HelloWorkflow",
                        "task_queue": f"{domain}-workflow-task-queue",
                        "sample_inputs": [
                            {"label": "happy_path", "input": {"name": "Temporal"}}
                        ],
                    }
                ],
                "observability": {"dashboard": False},
            },
            sort_keys=False,
        )
    )

    subprocess.run(
        [
            sys.executable,
            str(SCAFFOLD),
            "--name",
            domain,
            "--root",
            str(tmp_path),
        ],
        check=True,
        cwd=REPO_ROOT,
    )

    values = yaml.safe_load(
        (tmp_path / f"deploy/charts/{domain}-workers/values.yaml").read_text()
    )
    assert "autoscaling" not in values
    by_name = {w["name"]: w for w in values["workers"]}
    assert by_name["workflow"]["autoscaling"] == {
        "minReplicas": 2,
        "maxReplicas": 8,
        "targetBacklogPerReplica": 3,
        "slotScaleUpEnabled": True,
    }
    assert by_name["activity"]["autoscaling"] == {
        "minReplicas": 1,
        "maxReplicas": 12,
        "targetBacklogPerReplica": 7,
    }
    assert by_name["workflow"]["taskQueue"] == f"{domain}-workflow-task-queue"
    assert by_name["workflow"]["kind"] == "workflow"


def test_scaffold_domain_java_and_verify(tmp_path: Path) -> None:
    _seed_minimal_java_repo(tmp_path)
    _write_java_descriptor(tmp_path, JAVA_DOMAIN)

    subprocess.run(
        [
            sys.executable,
            str(SCAFFOLD),
            "--name",
            JAVA_DOMAIN,
            "--root",
            str(tmp_path),
        ],
        check=True,
        cwd=REPO_ROOT,
    )

    expected_files = [
        f"config/domains/{JAVA_DOMAIN}.yaml",
        f"libs/{JAVA_DOMAIN}/java/src/main/java/io/temporal/demo/scaffoldproofjava/shared/TemporalIds.java",
        f"apps/temporal/workers/java/{JAVA_DOMAIN}/workflow/src/main/java/io/temporal/demo/scaffoldproofjava/workflow/HelloWorkflowImpl.java",
        f"deploy/charts/{JAVA_DOMAIN}-workers/Chart.yaml",
    ]
    for rel in expected_files:
        assert (tmp_path / rel).is_file(), f"missing scaffolded file: {rel}"

    values = (tmp_path / f"deploy/charts/{JAVA_DOMAIN}-workers/values.yaml").read_text()
    assert 'command: ["python"' not in values

    env = {**os.environ, "DOMAIN_VERIFY_ROOT": str(tmp_path)}
    verify = subprocess.run(
        [sys.executable, str(VERIFY), JAVA_DOMAIN],
        env=env,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )
    assert verify.returncode == 0, verify.stdout + verify.stderr
