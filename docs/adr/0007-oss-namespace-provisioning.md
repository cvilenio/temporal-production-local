# ADR-0007: OSS namespace & search-attribute provisioning — shared spec, delivery plane

- **Status:** Accepted
- **Date:** 2026-06-24

## Context

ADR-0002 split infrastructure by plane: Terraform owns the control plane (kind cluster,
Temporal Cloud, ArgoCD install), ArgoCD/Helm owns everything running on the cluster
(including the self-hosted Temporal server, per ADR-0003). Checkpoint 0004 provisioned the
Temporal **Cloud** namespaces, retention, service accounts, API keys, and **search
attributes** declaratively with the `temporalio/temporalcloud` Terraform provider.

Local **OSS** ended up with the same logical feature set but a different, ad-hoc mechanism:
one-shot Compose init containers that hardcoded the search-attribute set inline. This created
two problems:

1. **Drift.** The orders search attributes (`OrderId`/`TraceId`/`OrderStatus`) were defined
   twice — in `compose/oss-server.yml` *and* in the Cloud `terraform.tfvars` — free to diverge.
2. **Not production-grade.** Imperative init containers are not how customers run self-hosted
   Temporal; they use declarative delivery (Helm/GitOps).

A natural-seeming "fix" — manage OSS namespaces in Terraform too, for symmetry — was rejected.

## Decision

**1. One shared, backend-agnostic spec is the single source of truth.**
`config/temporal/namespaces.yaml` defines namespace identity (domain), custom search
attributes, and per-env retention. Both backends read it:
- Cloud: `deploy/terraform/layers/cloud` reads it via `yamldecode()`, maps each domain×env to
  a `<domain>-<env>` namespace, and merges a small **Cloud-only overlay** (service account,
  API key, regions) on top. The overlay carries only fields with no OSS analog.
- OSS: `compose/scripts/render-oss-bootstrap.py` renders the spec to a shell-sourceable file
  the bootstrap container loops over (the `admin-tools` image has no `yq`/`jq`). On kind, this
  same spec feeds an Argo-managed Job (see below).

**2. OSS provisioning stays in the delivery plane — never Terraform.** The `temporalcloud`
provider is Cloud-only; there is no first-party Terraform provider for self-hosted namespace
config. The only Terraform path would be `null_resource` + `local-exec` shelling
`temporal operator` — non-declarative, with no reconciliation (the running frontend, not
state, is the source of truth). Rejected. This also keeps the ADR-0002 boundary intact.

**3. The production-grade OSS provisioner is an Argo-managed Job, not an init container.** On
kind, namespace + search-attribute creation is a Helm-templated Job (rendered from the shared
spec) run as an **ArgoCD PostSync hook** after the `temporal-server` Application is healthy —
desired state in Git, reconciled by Argo. The Compose init containers remain **only** as the
explicitly-labeled local non-prod convenience.

## Why the Cloud/OSS asymmetry is inherent

Cloud namespaces are control-plane SaaS objects — provisioned via a management API with no
server to run, so Terraform owns them. OSS namespaces live inside the **running frontend**
(gRPC) and can only be created after the server is up, so they are post-deploy config that
belongs to the plane running the server. The shared spec gives *config* equivalence without
pretending the *provisioning mechanism* is the same.

## Consequences

- Search attributes (and retention) are defined once; a change surfaces in both the Cloud
  `terraform plan` and the next OSS `poe up`. Drift is structurally prevented.
- The Cloud layer's input model changed from a full `namespaces` map to spec + `cloud_overlay`.
  The derived `for_each` keys/values are byte-identical to before, so `terraform plan` shows
  no changes (namespaces are `prevent_destroy`).
- Adding a business domain = add it to the spec, then add Cloud overlay entries; OSS picks it
  up via the renderer.
- The kind Argo bootstrap Job and OSS auth (ADR-0008) are follow-on work built with
  `layers/cluster`; the Compose path is the interim local story.

## Update (checkpoint 0015) — env axis retired; spec is domain-only

The spec's `domains.<d>.environments.{nonprod,prod}` nesting and the `oss.environment`
selector are removed. Each **domain** maps to a single `<domain>` namespace (e.g.
`ziggymart`) on both backends — Cloud and OSS namespace names now converge (OSS already used
the bare name). The `cloud_overlay` and the cloud layer outputs are re-keyed by `<domain>`;
the `cloud-namespace` module is unchanged (env vs domain was always a caller concern in
`namespaces.tf`). Rationale: the nonprod/prod axis demonstrated no Temporal feature; the axes
that do — **domain** (Nexus, per-domain auth) and **region** (multi-region HA) — are modeled
elsewhere. See ADR-0017's update note and the new checkpoint. (Cloud namespaces can't be
renamed in place, so the rename was a destroy+recreate via the module's `prevent_destroy`
escape hatch.)
