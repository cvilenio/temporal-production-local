"""Domain doctor tests — fault injection without cluster or Temporal."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
VERIFY = REPO_ROOT / "compose/scripts/verify-domains.py"


def _run_verify(
    root: Path, domain: str | None = None
) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(VERIFY)]
    if domain:
        cmd.append(domain)
    env = {**os.environ, "DOMAIN_VERIFY_ROOT": str(root)}
    return subprocess.run(
        cmd,
        env=env,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )


def _minimal_orders_tree(root: Path) -> None:
    """Copy the minimum real-repo paths the doctor needs for orders."""
    shutil.copytree(REPO_ROOT / "config", root / "config")
    shutil.copytree(REPO_ROOT / "libs/orders", root / "libs/orders")
    shutil.copytree(
        REPO_ROOT / "apps/temporal/workers/python/orders",
        root / "apps/temporal/workers/python/orders",
    )
    shutil.copytree(
        REPO_ROOT / "apps/temporal/workers/java/orders",
        root / "apps/temporal/workers/java/orders",
    )
    shutil.copytree(
        REPO_ROOT / "deploy/charts/orders-workers",
        root / "deploy/charts/orders-workers",
    )
    shutil.copytree(
        REPO_ROOT / "compose/observability/grafana/dashboards/orders",
        root / "compose/observability/grafana/dashboards/orders",
    )
    (root / "deploy/terraform/layers/cluster").mkdir(parents=True)
    shutil.copy(
        REPO_ROOT / "deploy/terraform/layers/cluster/variables.tf",
        root / "deploy/terraform/layers/cluster/variables.tf",
    )
    shutil.copy(REPO_ROOT / "pyproject.toml", root / "pyproject.toml")
    shutil.copy(REPO_ROOT / "settings.gradle", root / "settings.gradle")
    for lang in ("python", "java", "go"):
        (root / "images").mkdir(parents=True, exist_ok=True)
        shutil.copy(
            REPO_ROOT / f"images/{lang}.Dockerfile",
            root / f"images/{lang}.Dockerfile",
        )
    shutil.copy(
        REPO_ROOT / "config/domains/orders.yaml",
        root / "config/domains/orders.yaml",
    )


def test_verify_orders_passes(tmp_path: Path) -> None:
    _minimal_orders_tree(tmp_path)
    result = _run_verify(tmp_path, "orders")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "orders" in result.stdout


def test_verify_errors_on_missing_worker_dir(tmp_path: Path) -> None:
    _minimal_orders_tree(tmp_path)
    shutil.rmtree(tmp_path / "apps/temporal/workers/python/orders/workflow")
    result = _run_verify(tmp_path, "orders")
    assert result.returncode == 1
    assert "worker dir missing" in result.stdout + result.stderr
    assert "workflow" in result.stdout + result.stderr


def test_verify_errors_on_unknown_task_queue(tmp_path: Path) -> None:
    _minimal_orders_tree(tmp_path)
    desc = tmp_path / "config/domains/orders.yaml"
    text = desc.read_text()
    desc.write_text(text.replace("orders-workflow-task-queue", "orphan-task-queue"))
    result = _run_verify(tmp_path, "orders")
    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "orphan-task-queue" in combined
    assert "TaskQueue constants" in combined


def test_verify_warns_on_missing_sample_inputs(tmp_path: Path) -> None:
    _minimal_orders_tree(tmp_path)
    desc = tmp_path / "config/domains/orders.yaml"
    text = desc.read_text()
    desc.write_text(text.replace("sample_inputs:", "sample_inputs_removed:"))
    result = _run_verify(tmp_path, "orders")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "WARN" in result.stdout
    assert "sample_inputs" in result.stdout
    assert "console trigger" in result.stdout


def test_verify_rejects_obsolete_top_level_autoscaling(tmp_path: Path) -> None:
    _minimal_orders_tree(tmp_path)
    desc = tmp_path / "config/domains/orders.yaml"
    text = desc.read_text()
    desc.write_text(
        "autoscaling:\n  enabled: true\n" + text,
    )
    result = _run_verify(tmp_path, "orders")
    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "obsolete top-level 'autoscaling'" in combined


def test_verify_errors_on_invalid_autoscaling_bounds(tmp_path: Path) -> None:
    _minimal_orders_tree(tmp_path)
    desc = tmp_path / "config/domains/orders.yaml"
    text = desc.read_text()
    desc.write_text(
        text.replace(
            "minReplicas: 1\n      maxReplicas: 6",
            "minReplicas: 5\n      maxReplicas: 2",
        )
    )
    result = _run_verify(tmp_path, "orders")
    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "minReplicas" in combined
    assert "maxReplicas" in combined


def test_verify_warns_on_replicas_with_autoscaling(tmp_path: Path) -> None:
    _minimal_orders_tree(tmp_path)
    desc = tmp_path / "config/domains/orders.yaml"
    text = desc.read_text()
    desc.write_text(
        text.replace(
            "    dependency_group: workers\n    autoscaling:",
            "    dependency_group: workers\n    replicas: 2\n    autoscaling:",
            1,
        )
    )
    result = _run_verify(tmp_path, "orders")
    assert result.returncode == 0, result.stdout + result.stderr
    combined = result.stdout + result.stderr
    assert "WARN" in combined
    assert "both replicas and autoscaling" in combined
