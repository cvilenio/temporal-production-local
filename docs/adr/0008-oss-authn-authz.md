# ADR-0008: Auth model — Cloud API keys, OSS mTLS+JWT, by control-plane vs data-plane identity (DESIGN)

- **Status:** Proposed (design only; OSS build sequenced with `layers/cluster`)
- **Date:** 2026-06-24

## Context

Local OSS runs no-auth today (`TEMPORAL_TLS=false`, no key) — fine for a Compose dev loop, but
a production-readiness reference should secure self-hosted Temporal the way customers run it.
While designing OSS auth, two questions surfaced: (1) if OSS uses mTLS, should Cloud too? and
(2) should auth differ by *who is connecting* — automation (Terraform) vs workers?

The answer to both turns on separating three layers and two identity planes, rather than
treating "mTLS vs JWT" as one global either/or.

### Three layers (don't conflate)

| Layer | Job | Cloud | Self-hosted OSS |
|---|---|---|---|
| Transport | encrypt the wire (TLS) | always on (managed) | configure `tls` (frontend + internode) |
| Client identity | how a caller proves who it is | mTLS cert **or** API key | mTLS client cert and/or JWT bearer |
| Authorization | what that identity may do | service-account RBAC (API key) / CA-trust + cert filters (mTLS) | `ClaimMapper` + `Authorizer` plugins |

mTLS and JWT only compete at the **identity** layer. Transport TLS is non-negotiable in prod
either way. On self-hosted they **compose** (TLS transport + JWT claims for RBAC; the claim
mapper may also read the cert subject DN).

### Two identity planes (the split that matters)

- **Control-plane identity** — *manages* Temporal (create namespaces, search attributes, keys).
- **Data-plane identity** — *connects to* Temporal to run work (workers, clients).

These should be different credentials with different blast radius and rotation cadence,
regardless of mechanism.

## Decision

### Cloud — keep API keys; do NOT add a parallel mTLS path

- **Control plane (Terraform provider, Ops API, `tcld`, CLI): account-level API key** — the
  bootstrap account-admin key. This is **mandatory**: the Terraform provider and Ops API
  support only API-key auth. Tightly scope it; prefer disabling it between applies (carried
  from 0004's open questions).
- **Data plane (workers): least-privilege service-account API keys** — the current setup, and
  Temporal's documented default ("API keys with service accounts"). RBAC-tied and auditable.
- **mTLS for Cloud workers is supported but declined here.** A namespace is single-auth-mode
  (mTLS **or** API key for its clients; "both" is pre-release and drops HA). Cloud mTLS is
  *not* tied to RBAC (access = CA trust + cert filters by Common Name), adds PKI lifecycle
  burden, and cannot be used for the Terraform/Ops automation anyway. Reserve it for a real
  enterprise-PKI mandate; document, don't build.
- **Net:** the "API key for automation, mTLS for workers" split is native to Cloud (the planes
  are independent). We use API keys for *both* planes by choice — lowest maintenance, RBAC-tied.

##### Update (2026-06-25): the data plane splits into worker vs client identities

When orders-api moved onto kind, it became a *second* data-plane caller — the **client** that
starts/signals workflows, distinct from the **workers** that execute them. It gets its **own
least-privilege service-account API key**, not the worker key: separate credential, separate
blast radius and rotation, even though both happen to need namespace `write`. The
`cloud-namespace` Terraform module mints it optionally (`client_service_account_name`); the
cluster layer seeds it as the `orders-client-apikey` Secret, which orders-app's `connection`
consumes as `TEMPORAL_API_KEY`. The two-identity-planes principle (control vs data) thus refines
to **control / worker / client** — three credentials, each least-privilege.

#### Future Cloud mTLS demo (feasibility note — not built)

If we later want to demonstrate the mTLS worker path, it is feasible in this repo without any
public infrastructure:

- **No callback / no public cert service.** Cloud mTLS works by uploading your **CA
  certificate** (public cert, not the key) to the namespace; Cloud validates presented client
  certs against it and never calls back out (no OCSP/CRL/JWKS fetch). Docs: *"Temporal Cloud
  does not require an exchange of secrets."* A **self-signed** CA + leaf (OpenSSL/step/cfssl)
  is valid — generate locally, upload the CA, mount the leaf+key in the worker. (Contrast: the
  JWKS endpoint the *server* fetches is a self-hosted-JWT concern, in-cluster on kind, not this.)
- **Per-namespace, additive.** Auth mode is per namespace. Leave `ziggymart-{nonprod,prod}` on
  API keys and add a dedicated mTLS namespace (e.g. `ziggymart-mtls`) with an uploaded CA. The
  spec/`cloud_overlay` model extends with an `auth_method` field; the module grows an mTLS
  branch (`accepted_client_ca`, no `temporalcloud_apikey`). A single namespace being both
  api-key and mTLS is the pre-release flag — we'd use separate namespaces instead.
- **Same image, config-driven.** mTLS vs API key is injected credentials + a connect-time
  branch, not a build fork: API key → `TEMPORAL_API_KEY`; mTLS → mounted cert/key/CA the SDK
  loads into its TLS config. At deploy time the Worker Controller `Connection` already exposes
  `apiKeySecretRef` vs `mutualTLSSecretRef` (one field). The only real cost is the connect
  branch living once per SDK in each language's shared kernel (polyglot repo) — N helpers, not
  N images. This is the backend-agnostic connection contract paying off.

### OSS — mTLS transport (phase 1), JWT/OIDC authz (phase 2)

Local OSS ships with `noopAuthorizer` (allow-all, no auth) — there is no secure default; both
a `ClaimMapper` and `Authorizer` must be configured to lock it down. Build in two phases,
delivery-plane and Argo-managed (no init containers):

- **Phase 1 — transport mTLS.** `cert-manager` issues/rotates a CA + frontend/client certs
  (`tls.frontend` + `internode`, `requireClientAuth`). Biggest security win, self-contained.
- **Phase 2 — JWT/OIDC authorization.** `defaultJWTClaimMapper` + `defaultAuthorizer`, JWTs
  from a Helm-deployed OIDC issuer (e.g. Dex) or a reused corporate IdP, verified against JWKS
  via `frontend.authorization.jwtKeyProvider`. Claims are `<namespace>:<permission>` (read/
  write/worker/admin) → Temporal roles. This is the OSS analog of Cloud service-account RBAC.

OSS identity planes (no Terraform→Temporal path exists, per ADR-0007):
- **Control-plane identity** = the **Argo bootstrap Job** (creates namespace + search
  attributes). Once auth is on, it needs an **admin-scoped** identity (JWT with `admin` claims,
  or an mTLS cert the claim mapper maps to admin) to reach the secured frontend.
- **Data-plane identity** = **workers**, with mTLS transport + a **worker-scoped** JWT.

### Kubernetes pod identity (kind) — named KSAs now, Workload Identity Federation deferred

The two identity planes above are about *Temporal* auth. On kind there is a third, lower
identity: the **Kubernetes ServiceAccount** each worker pod runs as. By default the Worker
Controller's pods would inherit the namespace `default` KSA with its token auto-mounted.

- **Decision: one dedicated KSA per worker profile, `automountServiceAccountToken: false`**
  (`deploy/charts/orders-workers/templates/serviceaccount.yaml`; `serviceAccountName` set on
  the `WorkerDeployment` pod template — supported by the v1alpha1 CRD podSpec at the pinned
  app 1.7.0). The win is blast-radius: these workers only egress to the Temporal frontend and
  never call the k8s API, so not mounting the token removes the one thing a `default` SA
  exposes — a projected token a compromised pod could replay against the API server.
- **Temporal Cloud auth does not flow through the KSA.** It is the API-key Secret on the
  `Connection` (above). The KSA isolates k8s-side permissions only; the two are orthogonal.
- **Workload Identity Federation is intentionally NOT emulated here.** WIF federates a KSA →
  cloud IAM (GKE Workload Identity → GCP SA, or IRSA). It has no faithful local target: (1)
  Temporal Cloud does not accept GCP/OIDC-federated identities in the worker auth path — it
  takes an API key or mTLS, neither of which WIF replaces; (2) the only other prod-GKE use,
  pulling from Artifact Registry / Secret Manager, is served locally by **zot over plain HTTP
  with no auth** (ADR-0011/0013), so there is no cloud IAM to federate to. Half-emulating WIF
  would require inventing a cloud IAM the air-gap-local delivery model deliberately omits.
  Documented as the prod-GKE overlay a real deployment adds; not built.

### Parity is at the contract, not the mechanism

Cloud and OSS keep **one connection contract**, two secret types. The Worker Controller
`Connection` already accepts either `mutualTLSSecretRef` **or** `apiKeySecretRef` (one per
Connection): Cloud → API-key Secret; OSS → mTLS Secret. Apps/workers stay backend-agnostic;
only the Secret type and the cluster-layer wiring differ. Do not force identical mechanisms.

## Argo ordering — how reliability is enforced

"Production-grade" means a dependency is **healthy** before dependents start. ArgoCD enforces
this with three stacked mechanisms:

- **Sync waves** (`argocd.argoproj.io/sync-wave: "<int>"`): apply wave-ascending; wait for
  every resource in a wave to report **Healthy** before the next ("priority levels").
- **Sync phases / hooks** (`PreSync → Sync → PostSync → SyncFail`): the namespace/search-attr
  bootstrap Job (ADR-0007) is a **PostSync** hook — runs only after the server is healthy.
- **Health assessment**: the gate under both. CRDs (CNPG `Cluster`, cert-manager
  `Certificate`, Temporal `Connection`/`WorkerDeployment`) need a health check or Argo treats
  them Healthy immediately and the gate becomes a no-op — the main trap to avoid.

Illustrative wave layout for the secured OSS stack:

```
wave -2  cert-manager Issuer + Certificates Healthy        (mTLS material, phase 1)
wave -1  CNPG Postgres Cluster Healthy
wave  0  temporal-server (schema jobs → frontend Healthy, TLS + JWT configured)
wave  1  OIDC issuer (Dex) Healthy                          (phase 2)
PostSync namespace + search-attribute bootstrap Job (shared spec; admin identity)
wave  2  orders-workers / apps (need namespace + auth live; worker identity)
```

## Maintenance tradeoffs

- **API key** (Cloud, both planes): secret-manager storage, rotate ≤90d with dual-key overlap,
  one per service. No PKI. Lowest ceremony; RBAC-tied → auditable. Required for Terraform/Ops.
- **mTLS** (OSS transport; optional Cloud workers): full cert lifecycle — CA + leaf issue/
  distribute/rotate (client ~quarterly, CA ~annually); **expired CA = total outage**, so expiry
  monitoring is mandatory. cert-manager automates this *on k8s*; off-k8s it is manual.
- **JWT/OIDC** (OSS authz): run or integrate an IdP, expose JWKS, map claims. Marginal cost if
  reusing a corporate IdP; another component if self-running Dex. Gives real RBAC.

## Consequences

- Cloud is unchanged (API keys + least-privilege service accounts); no redundant mTLS path.
- OSS auth is a kind-only, delivery-plane workstream in two phases; the Compose path stays
  no-auth and labeled as such.
- Introduces cert-manager (+ later an OIDC issuer) as cluster dependencies — standard, Helm-/
  Argo-deployable.
- The control-plane/data-plane identity split is the organizing principle: the bootstrap Job
  carries an admin identity, workers carry a least-privilege one — on both backends.
- Revisit status to Accepted when the OSS phases are implemented with `layers/cluster`.

## Update (checkpoint 0015) — auth divergence is the DOMAIN axis, not env

With the nonprod/prod env split retired (ADR-0017), auth-method divergence (API key vs mTLS,
strictness) is now expressed **per domain** rather than per env — each domain namespace
carries its own least-privilege service account + key in the `cloud_overlay`, and a future
`auth_method` field would sit there. The control-plane/data-plane identity split is unchanged;
there is simply one namespace per domain instead of one per domain×env.
