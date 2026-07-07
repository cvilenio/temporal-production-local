"""Domain descriptor helpers — load config/domains/*.yaml and resolve shared contracts.

The domain descriptor is the within-domain wiring contract (ADR-0021): every party
touching a domain (workers, starter, console, codec-server) resolves the same data
converter from the descriptor rather than re-deciding it.
"""

from __future__ import annotations

import os
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from temporalio.contrib.pydantic import pydantic_data_converter

if TYPE_CHECKING:
    from temporalio.converter import DataConverter

REPO_ROOT = Path(__file__).resolve().parents[4]


def domains_dir() -> Path:
    """Root directory for domain descriptors (config/domains in dev; mounted in console)."""
    override = os.environ.get("DOMAIN_DESCRIPTORS_DIR", "").strip()
    if override:
        return Path(override)
    return REPO_ROOT / "config" / "domains"


@cache
def load_domain_descriptor(domain: str) -> dict:
    """Load config/domains/<domain>.yaml."""
    root = domains_dir()
    path = root / f"{domain}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"domain descriptor not found: {path}")
    data = yaml.safe_load(path.read_text()) or {}
    if data.get("domain") != domain:
        raise ValueError(f"{path}: 'domain' field must match filename ({domain!r})")
    return data


def domain_for_namespace(namespace: str) -> str | None:
    """Map a Temporal namespace handle to a domain key (bare name before Cloud suffix)."""
    bare = namespace.split(".", 1)[0]
    root = domains_dir()
    if not root.is_dir():
        return None
    for path in root.glob("*.yaml"):
        desc = yaml.safe_load(path.read_text()) or {}
        if desc.get("domain") == bare:
            return bare
    return None


def list_domain_descriptors(*, exclude: set[str] | None = None) -> list[dict]:
    """Load every domain descriptor under domains_dir(), optionally skipping keys."""
    root = domains_dir()
    if not root.is_dir():
        return []
    skip = exclude or set()
    out: list[dict] = []
    for path in sorted(root.glob("*.yaml")):
        desc = yaml.safe_load(path.read_text()) or {}
        domain = desc.get("domain") or path.stem
        if domain in skip:
            continue
        if desc.get("domain") != domain:
            continue
        out.append(desc)
    return out


def resolve_data_converter(name: str) -> DataConverter:
    """Resolve a descriptor `data_converter` value to a Temporal DataConverter."""
    if name in ("default", "pydantic", "json"):
        return pydantic_data_converter
    raise ValueError(
        f"unknown data_converter {name!r} — add a resolver or set data_converter: default"
    )


def data_converter_for_domain(domain: str) -> DataConverter:
    """Load a domain descriptor and return its DataConverter."""
    descriptor = load_domain_descriptor(domain)
    ref = str(descriptor.get("data_converter") or "default")
    return resolve_data_converter(ref)


def data_converter_for_namespace(namespace: str) -> DataConverter:
    """Resolve the DataConverter for a Temporal namespace via its domain descriptor.

    Intended for Phase B generic console (repo-local dev), NOT for in-cluster workers
    or starters — those read ``TEMPORAL_DATA_CONVERTER`` from settings (injected by
    the chart from the descriptor at deploy time). The console will need descriptors
    mounted or packaged as data before calling this at runtime.
    """
    domain = domain_for_namespace(namespace)
    if domain is None:
        raise FileNotFoundError(
            f"no domain descriptor for namespace {namespace!r} under {domains_dir()}/"
        )
    return data_converter_for_domain(domain)
