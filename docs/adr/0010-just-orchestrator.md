# ADR-0010: `just` as the top-level task orchestrator

- **Status:** Accepted
- **Date:** 2026-06-24

## Context

Task running lived entirely in `poe` (poethepoet), defined in `pyproject.toml`. `poe` is an
excellent Python task runner — it knows the `uv` environment and sequences tasks cleanly — but it
is the wrong thing to be the *repo's front door* for a repo whose stated goal (ARCHITECTURE.md) is
polyglot growth (Go, TS, Java alongside Python):

- Entry points buried in `pyproject.toml` signal "Python repo"; a Go/TS dev won't look there.
- Running *any* task requires the Python toolchain (`uv`), even cluster/infra work that isn't Python.
- `make test` is universal muscle memory; `uv run poe test` is not.

The cluster work added genuinely non-Python tasks (kind, terraform, registry), making the
Python-branded front door an active smell.

## Decision

Add a language-agnostic **`just`** front door (`justfile`) as the recognizable entry point. `just`
is a fast, single-binary Rust task runner — the uv-spirit choice: modern, quick, zero ceremony.

- `just` recipes **delegate to `poe`** for Python work (`just up` → `uv run poe up`) and **shell
  out** for cluster/infra work (`just cluster-up` → `deploy/kind/cluster-up.sh`).
- `poe` stays as the Python task layer in `pyproject.toml` — unchanged, still the right tool for
  Python internals. `just` wraps it; it does not replace it.
- `just --list` is the discovery surface. As Go/TS/Java land, their native runners hang off the
  same recipes — no Python toolchain needed to drive the repo.

`mise` was considered as the larger consolidation play (Rust too; unifies toolchain *versions* +
tasks + env, and would also replace `.python-version`/`.tool-versions`). Deferred: for a pure fast
front door `just` is cleaner, and it can delegate to `mise` later if toolchain management is wanted.
`make` was rejected: maximum ubiquity but arcane syntax and weak cross-platform behavior.

## Consequences

- One recognizable, language-agnostic interface (`just <recipe>`); the Python layer is an
  implementation detail behind it.
- A new dev dependency: contributors install `just` (`brew install just`, or via `mise`/cargo).
  The bigger toolchain-pinning story (mise) remains open.
- Two task files (`justfile` + `pyproject.toml [tool.poe.tasks]`). The split is deliberate:
  `justfile` = cross-language front door, `poe` = Python internals. Keep new Python tasks in `poe`
  and expose them through a thin `just` recipe.
- CI logic stays in `poe` (`poe ci`); a future GitHub Actions workflow would be a thin wrapper
  calling `just ci`/`poe ci`, so local and remote run identical steps. Not authored yet.
