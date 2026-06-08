# Contributing

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
