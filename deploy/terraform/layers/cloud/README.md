# layers/cloud — Temporal Cloud base layer

Provisions one namespace + service account (+ optional worker API key) + custom search
attributes **per domain** (`ziggymart`, … keyed by `<domain>` — no nonprod/prod env axis,
ADR-0017; business domains coexist on the one account) via a single `for_each` over the
spec × `cloud_overlay`. API-key auth only (`api_key_auth = true`; no certificate auth).
The account id comes from
`TF_VAR_account_id` (`.secrets/account.env`), never committed.

This is the **base control-plane layer**. It depends on only the `temporalcloud`
provider — `terraform init` here pulls nothing for kind/Kubernetes/Helm, and the layer
applies with no cluster present. It writes nothing to compose or Kubernetes; consumers
read its outputs (see "Handoff").

## Prerequisites

- A bootstrap (account-level, namespace-admin) Temporal Cloud API key.
- `terraform >= 1.6`.

## Apply

```bash
cd deploy/terraform/layers/cloud
cp terraform.tfvars.example terraform.tfvars   # git-ignored; edit if needed
source ../../../../.secrets/account.env        # TF_VAR_account_id (account id, not in git)
source ../../../../.secrets/keys/bootstrap.env # TEMPORAL_CLOUD_API_KEY (bootstrap key)

terraform init                 # confirm only temporalio/temporalcloud is pulled
terraform fmt -check
terraform validate
terraform plan -out=cloud.plan # per namespace: 1 ns + 1 SA + 1 key + N search attributes
terraform apply cloud.plan     # apply the SAVED plan
```

To bring up only one namespace first, trim `cloud_overlay` to that domain, apply, then add
the rest and apply again.

## Handoff to consumers (no layer coupling)

The layer emits outputs keyed by **domain** (`ziggymart`, …). Wire a
consumer yourself:

```bash
terraform output -json endpoints                   # TEMPORAL_ADDRESS per namespace
terraform output -json namespace_handles           # TEMPORAL_NAMESPACE per namespace
terraform output -json api_key_tokens              # SENSITIVE: worker key per namespace
```

**Host cloud profile (now):** write a profile into the hardened secrets dir,
`.secrets/keys/cloud.env`, with `TEMPORAL_ADDRESS=<endpoints[ziggymart]>`,
`TEMPORAL_NAMESPACE=<namespace_handles[ziggymart]>`, `TEMPORAL_TLS=true`,
`TEMPORAL_API_KEY=<api_key_tokens[ziggymart]>`, and (optional)
`TEMPORAL_CLOUD_OPS_API_KEY=<observer_api_key_token>`. The `poe up-cloud-kind` task sources
this file (the kind+Cloud host plane; it also gives the console its read-only Cloud liveness
+ inventory creds). App code already supports it
(`libs/orders/python/orders/services/temporal.py`). Outputs are keyed by `<domain>` (no env
axis). See `docs/RUNMODES.md`.

**Custom search attributes** are declared here as namespace setup — the
`search_attributes` map per namespace creates `temporalcloud_namespace_search_attribute`
resources (the orders workload needs `OrderId`/`TraceId`/`OrderStatus`). `terraform apply`
provisions them; no out-of-band step. The OSS equivalent is the temporal-search-attributes
bootstrap container in `compose/oss-server.yml`. (`tcld namespace search-attributes add`
remains a manual fallback, but is not part of normal setup.)

**Kubernetes (later):** the **cluster layer** (not this one) reads these outputs and
creates the k8s Secret that the worker chart references
(`deploy/charts/orders-workers/values.yaml` `connection.apiKeySecret`).

## State is a secret

State lives at **`.secrets/terraform/cloud.tfstate`** (local backend; the `.secrets/`
dir is `chmod 700` and git-ignored). It holds the worker API key **in plaintext**, and
there is no remote backend by design (no cloud beyond Temporal Cloud). Do not commit or
share it. See `.secrets/README.md`.

### Recovery if local state is lost

Temporal Cloud is the source of truth. Re-import the declarative resources:

```bash
terraform import 'module.namespaces["ziggymart"].temporalcloud_namespace.this'        <namespace-id>
terraform import 'module.namespaces["ziggymart"].temporalcloud_service_account.workers' <service-account-id>
# repeat per domain; search attributes import as <namespace-id>/<name>
```

The **API key secret cannot be recovered** (shown once at creation). Rotate instead:
`terraform apply -replace='module.namespaces["ziggymart"].temporalcloud_apikey.workers[0]'`,
then re-distribute. Treat tfstate as a convenience cache, not the only copy.

### Optional hardening (not enabled)

To track state in git safely without any cloud, encrypt it with SOPS + a local `age`
key. Not configured here; mentioned for completeness.

## Tearing down

Namespaces have `prevent_destroy = true` (single state guards every domain). To
intentionally remove or rename one (Cloud namespaces can't be renamed in place), target it
or temporarily set the lifecycle guard to `false` in
`deploy/terraform/modules/cloud-namespace/namespace.tf`, apply, then restore it.

> **Gotcha — search attributes block a namespace destroy.** Temporal Cloud does **not**
> support deleting an individual search attribute, so a destroy fails on the namespace's
> `temporalcloud_namespace_search_attribute` children. Before the destroy apply, drop them
> from state — `terraform state rm 'module.namespaces["<domain>"].temporalcloud_namespace_search_attribute.this["<Attr>"]'`
> — they are removed along with the namespace via `DeleteNamespace` (which *is* supported).
