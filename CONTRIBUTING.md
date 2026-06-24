# Contributing

## Pre-commit sensitive-content check

This repo is internal Temporal work made public, so a pre-commit hook guards against
leaking company-sensitive material (internal hostnames, architecture, dashboards,
customer data, credentials, hiring/interview references). It lives at
[`.githooks/pre-commit`](.githooks/pre-commit) and reviews the added lines of each
staged commit in two layers: a fast deterministic pattern scan, and an AI review via
the [`claude`](https://docs.claude.com/en/docs/claude-code) CLI (skipped if `claude`
is not installed). A hit in either layer blocks the commit.

Git does not enable repo-tracked hooks automatically. **After cloning, run once:**

```
git config core.hooksPath .githooks
```

The committed deterministic list is intentionally **generic** (only universal secret
shapes). Keep org- or project-specific markers in a local denylist that is **gitignored
and never committed**, so the public repo never contains the very terms it guards against:

```
# .githooks/patterns.local  — one POSIX extended-regex per line, '#' lines ignored
your-internal-domain-fragment
internal-project-name
```

The hook loads `.githooks/patterns.local` automatically if present. Add new markers there,
not to the committed hook.

Escape hatches (use your judgement — a block can be a false positive):

- `git commit --no-verify` — bypass for one commit
- `SKIP_SENSITIVE_CHECK=1 git commit …` — bypass via environment
- `SENSITIVE_CHECK_MODEL=<model-id> git commit …` — override the AI model

## Commit & PR conventions

This repo follows the same lightweight convention as
[`temporalio/sdk-python`](https://github.com/temporalio/sdk-python): clean imperative
subjects enforced by review and squash-merge, **not** strict Conventional Commits.

### Subject line

- **Imperative mood, sentence case.** "Add order cancellation reason", not
  "Added..." / "adds..." / "add...".
- **No trailing period.** Keep it under ~72 characters.
- **Optional lowercase scope prefix** when it adds clarity. Use loosely, only the
  obvious ones — not a required taxonomy:
  - `ci:` build / workflow / pipeline changes
  - `docs:` documentation-only changes
  - `<component>:` e.g. `orders:`, `console:`, `observability:`
- **Do not** use Conventional Commits types (`feat:` / `fix:` / `chore:` …). They are
  not used upstream and create false structure here.

Good:

```
Add deterministic order ID derived from idempotency key
orders: Narrow retry policy on payment activity
ci: Key Rust cache on resolved Python version
docs: Fix Workflow Streams link
```

Avoid:

```
feat: add order id          # no Conventional Commits types
Added order id.             # past tense + trailing period
update stuff                # not imperative, not specific
```

### Body (optional)

Wrap at ~72 columns. Explain **why**, not what — the diff shows what.

### Merging

PRs are **squash-merged**. The PR title becomes the commit subject and must follow the
rules above; GitHub appends the PR number (e.g. `(#42)`) automatically.

### Changelog

User-facing changes (new feature, behavior/breaking change, deprecation, notable bug or
security fix) get a short high-level entry under `## [Unreleased]` in `CHANGELOG.md`
([Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format: Added / Changed /
Deprecated / Breaking Changes / Fixed / Security). Internal-only changes (refactors,
tests, CI, docs) need no entry. Optional for this demo repo, but keep it if present.

## Terraform file layout

Terraform reads every `*.tf` in a directory as one merged config, so file
boundaries are purely for human readers. Use them.

- **No `main.tf`.** A catch-all file named for nothing tells a reader nothing.
  Each file is named for the bundle of resources it holds.
- **Scope by concern, not by resource type.** A file groups resources that share
  a purpose; it is not one-file-per-`resource`. `orders-namespace.tf` holding a
  `kubernetes_namespace` plus the Secret seeded into it is right; splitting those
  two apart, or lumping them with unrelated ArgoCD resources, is not.
- **Name the file for its contents.** The name should answer "what's in here?"
  without opening it. Prefer specific over generic — `third-party-versions.tf`,
  not `dependencies.tf` (dependencies on what? of what kind?). `argocd.tf`,
  `applications.tf`, `registry-proxy.tf`, `remote-state.tf` are good; `infra.tf`,
  `resources.tf`, `misc.tf` are `main.tf` by another name.
- **Keep the conventional meta-files** as their own files: `variables.tf`,
  `outputs.tf`, `providers.tf`, `versions.tf`, `backend.tf`. These are already
  content-named and every reader expects them.
- **`locals` live with the resources they serve.** Co-locate a `locals` block in
  the file whose resources consume it. Extract shared locals into their own
  content-named file (e.g. `third-party-versions.tf`) only when more than one
  file reads them.

Filenames use hyphens (`remote-state.tf`), matching the existing layers/modules.
