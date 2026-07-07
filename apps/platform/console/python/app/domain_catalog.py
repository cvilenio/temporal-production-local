"""Load generic trigger catalog entries from config/domains/*.yaml (Phase B).

The orders flagship panel stays on scenarios.py — ziggymart (and any configured
excludes) are omitted from this catalog so the generic path stays additive.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from appkit.domains import list_domain_descriptors


@dataclass(frozen=True)
class WorkflowSample:
    label: str
    input: Any


@dataclass(frozen=True)
class CatalogWorkflow:
    type: str
    task_queue: str
    samples: tuple[WorkflowSample, ...]


@dataclass(frozen=True)
class CatalogDomain:
    domain: str
    language: str
    data_converter: str
    workflows: tuple[CatalogWorkflow, ...]
    workflow_task_queue: str | None
    activity_task_queue: str | None


def load_catalog(*, exclude_domains: set[str]) -> list[CatalogDomain]:
    """Build the console trigger catalog from domain descriptors."""
    catalog: list[CatalogDomain] = []
    for desc in list_domain_descriptors(exclude=exclude_domains):
        domain = str(desc["domain"])
        workers = desc.get("workers") or []
        wf_queue = next(
            (w["task_queue"] for w in workers if w.get("kind") == "workflow"), None
        )
        act_queue = next(
            (w["task_queue"] for w in workers if w.get("kind") == "activity"), None
        )
        workflows: list[CatalogWorkflow] = []
        for wf in desc.get("workflows") or []:
            wf_type = str(wf.get("type") or "")
            task_queue = str(wf.get("task_queue") or wf_queue or "")
            samples = tuple(
                WorkflowSample(label=str(s.get("label") or "default"), input=s.get("input"))
                for s in (wf.get("sample_inputs") or [])
            )
            if wf_type and task_queue and samples:
                workflows.append(
                    CatalogWorkflow(type=wf_type, task_queue=task_queue, samples=samples)
                )
        if not workflows:
            continue
        catalog.append(
            CatalogDomain(
                domain=domain,
                language=str(desc.get("language") or "python"),
                data_converter=str(desc.get("data_converter") or "default"),
                workflows=tuple(workflows),
                workflow_task_queue=wf_queue,
                activity_task_queue=act_queue,
            )
        )
    return catalog


def catalog_as_json(catalog: list[CatalogDomain]) -> list[dict[str, Any]]:
    return [
        {
            "domain": d.domain,
            "language": d.language,
            "data_converter": d.data_converter,
            "workflow_task_queue": d.workflow_task_queue,
            "activity_task_queue": d.activity_task_queue,
            "workflows": [
                {
                    "type": w.type,
                    "task_queue": w.task_queue,
                    "samples": [
                        {"label": s.label, "input": s.input} for s in w.samples
                    ],
                }
                for w in d.workflows
            ],
        }
        for d in catalog
    ]
