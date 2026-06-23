# .secrets/ — local sensitive material (never committed)

Everything sensitive that lives on a developer machine goes here. Git ignores the
**contents** of this directory; only this README and the `.gitkeep` placeholders are
tracked, so the layout exists on a fresh clone. Keep the directory `chmod 700`.

## Layout

| Path | Holds |
|---|---|
| `keys/` | API key material — the Temporal Cloud **bootstrap** key, and filled-in connection profiles like `cloud-nonprod.env` (the worker API key + endpoint). |
| `terraform/` | Terraform **state** for layers that use a local backend (e.g. `cloud.tfstate` from `deploy/terraform/layers/cloud`). State contains live secrets — treat the file as a credential. |

## Why state lives here

The demo deliberately uses no cloud resources outside Temporal Cloud, so there is no
remote state backend (S3/GCS). The Cloud layer's local backend points at
`.secrets/terraform/cloud.tfstate`, keeping every secret-bearing artifact under one
hardened directory.

## Rules

- Never commit contents. The git-ignore rules + the pre-commit secret gate enforce this;
  do not work around them.
- Re-create on a new machine: `mkdir -p .secrets/{keys,terraform} && chmod 700 .secrets`
  (already present from this README's `.gitkeep`s).
- If `cloud.tfstate` is lost, namespaces/service-accounts are recoverable via
  `terraform import`; the API key secret is not — rotate it. See
  `deploy/terraform/layers/cloud/README.md`.
