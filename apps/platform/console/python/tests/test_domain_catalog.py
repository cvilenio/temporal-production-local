from pathlib import Path

import pytest
from app.domain_catalog import load_catalog


def test_catalog_excludes_orders_domain():
    catalog = load_catalog(exclude_domains={"orders"})
    domains = {entry.domain for entry in catalog}
    assert "orders" not in domains


def test_catalog_loads_workflow_samples_from_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    desc_dir = tmp_path / "domains"
    desc_dir.mkdir()
    (desc_dir / "demo.yaml").write_text(
        """
domain: demo
data_converter: default
workers:
  - profile: workflow
    language: python
    kind: workflow
    deployment_name: demo-workflow
    task_queue: demo-workflow-task-queue
workflows:
  - type: HelloWorkflow
    task_queue: demo-workflow-task-queue
    sample_inputs:
      - label: happy_path
        input:
          name: World
"""
    )
    monkeypatch.setenv("DOMAIN_DESCRIPTORS_DIR", str(desc_dir))
    from appkit.domains import load_domain_descriptor

    load_domain_descriptor.cache_clear()

    catalog = load_catalog(exclude_domains=set())
    assert len(catalog) == 1
    assert catalog[0].domain == "demo"
    assert catalog[0].workflows[0].samples[0].input == {"name": "World"}
