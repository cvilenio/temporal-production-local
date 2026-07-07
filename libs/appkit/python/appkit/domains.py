"""Domain descriptor helpers — load config/domains/*.yaml and resolve shared contracts.

The domain descriptor is the within-domain wiring contract (ADR-0021): every party
touching a domain (workers, starter, console, codec-server) resolves the same data
converter from the descriptor rather than re-deciding it.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from temporalio.contrib.pydantic import pydantic_data_converter

if TYPE_CHECKING:
    from temporalio.converter import DataConverter

REPO_ROOT = Path(__file__).resolve().parents[4]
DOMAINS_DIR = REPO_ROOT / "config" / "domains"


@cache
def load_domain_descriptor(domain: str) -> dict:
    """Load config/domains/<domain>.yaml."""
    path = DOMAINS_DIR / f"{domain}.yaml"
    if not path.is_file():
        raise FileNotFoundError(
            f"domain descriptor not found: {path.relative_to(REPO_ROOT)}"
        )
    data = yaml.safe_load(path.read_text()) or {}
    if data.get("domain") != domain:
        raise ValueError(
            f"{path.relative_to(REPO_ROOT)}: 'domain' field must match filename ({domain!r})"
        )
    return data


def domain_for_namespace(namespace: str) -> str | None:
    """Map a Temporal namespace handle to a domain key (bare name before Cloud suffix)."""
    bare = namespace.split(".", 1)[0]
    if not DOMAINS_DIR.is_dir():
        return None
    for path in DOMAINS_DIR.glob("*.yaml"):
        desc = yaml.safe_load(path.read_text()) or {}
        if desc.get("domain") == bare:
            return bare
    return None


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
    """Resolve the DataConverter for a Temporal namespace via its domain descriptor."""
    domain = domain_for_namespace(namespace)
    if domain is None:
        return pydantic_data_converter
    return data_converter_for_domain(domain)
