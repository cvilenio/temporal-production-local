# layers/cloud — Temporal Cloud base layer

Provisions one namespace + service account (+ optional worker API key) + custom search
attributes **per namespace** (`ziggymart-nonprod`, `ziggymart-prod`, … keyed by full name
so business domains coexist) via a single `for_each` over `var.namespaces`. API-key auth
only (`api_key_auth = true`; no certificate auth). The account id comes from
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

To bring up only one namespace first, trim `namespaces` to that key, apply, then add the
rest and apply again.

## Handoff to consumers (no layer coupling)

The layer emits outputs keyed by **namespace name** (`ziggymart-nonprod`, …). Wire a
consumer yourself:

```bash
terraform output -json endpoints                   # TEMPORAL_ADDRESS per namespace
terraform output -json namespace_handles           # TEMPORAL_NAMESPACE per namespace
terraform output -json api_key_tokens              # SENSITIVE: worker key per namespace
```

**Compose cloud profile (now):** write a profile into the hardened secrets dir, e.g.
`.secrets/keys/cloud-nonprod.env`, with `TEMPORAL_ADDRESS=<endpoints[ziggymart-nonprod]>`,
`TEMPORAL_NAMESPACE=<namespace_handles[ziggymart-nonprod]>`, `TEMPORAL_TLS=true`,
`TEMPORAL_API_KEY=<api_key_tokens[ziggymart-nonprod]>`. The `poe up-cloud` task sources
this file. App code already supports it
(`libs/orders/python/orders/services/temporal.py`). See `docs/RUNMODES.md`.

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
terraform import 'module.namespaces["ziggymart-nonprod"].temporalcloud_namespace.this'        <namespace-id>
terraform import 'module.namespaces["ziggymart-nonprod"].temporalcloud_service_account.workers' <service-account-id>
# repeat per namespace; search attributes import as <namespace-id>/<name>
```

The **API key secret cannot be recovered** (shown once at creation). Rotate instead:
`terraform apply -replace='module.namespaces["ziggymart-nonprod"].temporalcloud_apikey.workers[0]'`,
then re-distribute. Treat tfstate as a convenience cache, not the only copy.

### Optional hardening (not enabled)

To track state in git safely without any cloud, encrypt it with SOPS + a local `age`
key. Not configured here; mentioned for completeness.

## Tearing down

Namespaces have `prevent_destroy = true` (single state guards prod). To intentionally
remove one, target it or temporarily lift the lifecycle guard in
`deploy/terraform/modules/cloud-namespace/main.tf`.
