# 0009 — ergonomic renames: kind node/registry names, console → platform-console, apps taxonomy

- **Status:** **LANDED (2026-06-24).** Working tree only — not yet committed or pushed.
- **Date:** 2026-06-24
- **ADRs:** none new. Updated ADR-0001 (apps taxonomy), ADR-0014 + ADR-0015 (console name).
  The taxonomy rationale was deliberately put in a new `apps/README.md` rather than its own
  ADR (the decision is small and self-evident once stated).

## Why

Pure ergonomics before resuming the main goals (from 0008's "Next"). Three name collisions /
misnomers made the repo harder to read:

1. **kind node containers were named `…-worker`** (`temporal-platform-worker`/`-worker2`),
   which collides with *Temporal* workers — the most overloaded word in the repo.
2. **The local registry was `kind-registry`**, implying a kind-managed object. It's an
   independent zot container (own `docker run`, volume, restart policy) merely *joined* to the
   kind network — and the repo frames it as emulating GKE + Artifact Registry.
3. **`retail-demo-console`** named a UI that is becoming a general host-plane console spanning
   business domains, not an orders/retail demo. And `apps/demo/` was a *lifecycle* label, not a
   concern — it lumped the operator console with a mock external dependency.

## Done this session

- **kind cluster `temporal-platform` → `kind`; registry `kind-registry` → `artifact-registry`.**
  kind hardcodes the `-control-plane`/`-worker` suffix from node role (no override), so the
  cluster-name *prefix* is the only lever. Result: `kind-control-plane`, `kind-worker`,
  `kind-worker2` (reads as "kind nodes", distinct from Temporal workers); kubeconfig context
  `kind-kind`. Renaming the registry off the `kind-` prefix avoided a new collision with the
  node names. Files: `justfile` (`cluster_name`, `kubeconfig`, `registry_name`), both
  `deploy/kind/cluster-{up,down}.sh`, `deploy/terraform/layers/cluster/variables.tf`
  (`kubeconfig_path`, `kube_context` → `kind-kind`, `registry_service`), `docker-compose.yml`
  (Headlamp kubeconfig path), docs (`adr-0011`, `adr-0014`, `RUNMODES.md` volume name).
  Old warm state (`kind-registry` container + `kind-registry-data` volume + stale kubeconfigs)
  removed manually — the renamed scripts wouldn't reap them.
- **Console `retail-demo-console` → `platform-console`** (machine id: compose service +
  `container_name` + `APP_GROUP` + uv dep-group + `docker_status` key + self-probe DNS +
  `architecture.html` status key + `uv.lock` regen). **Brand "Retail Demo Console" → "Platform
  Console"** (FastAPI title, `base.html` `<title>`+`<h1>`, console `README.md`). **Logo** swapped
  Lucide `store` → `layout-dashboard`.
- **Apps taxonomy re-cut on an ownership + required-to-run axis; `apps/demo/` eliminated.**
  `git mv apps/console → apps/platform/console`, `apps/demo/mock-api → apps/business/mock-api`.
  New three classes: `temporal/` (substrate, required to execute), `platform/` (operability
  tooling, run by a platform team), `business/` (Temporal-agnostic domain + simulated
  integrations). Path refs updated (`pyproject.toml` pyright roots, `docker-compose.yml` two
  `APP_PATH`s). Taxonomy docs rewritten: `adr-0001`, `ARCHITECTURE.md`, top-level `README.md`.
  New **`apps/README.md`** reinforces the rationale.

## Verification

- **`just cluster-up` (live, exit 0):** nodes `kind-control-plane` / `kind-worker` /
  `kind-worker2`; registry `artifact-registry`; context `kind-kind`; kubeconfig
  `.secrets/kube/kind.kubeconfig`; in-cluster Service `artifact-registry.kube-public.svc:5000`
  created. Cluster left **up** (the supported kind + Cloud resting state).
- **`docker compose build platform-console mock-api`:** both images build at the new
  `apps/platform/console/python` and `apps/business/mock-api/python` paths.
- **Clean `just up` (Compose-OSS, exit 0):** `platform-console` container **(healthy)**,
  `GET /healthz` → `{"status":"ok"}`; `temporal` healthy; all app + worker containers up. The
  console's `/architecture` page renders "Platform Console" / `platform-console` — no stale
  `retail-demo-console`.
- **Static:** `terraform fmt -check` clean, `just --list` parses, `docker compose config -q`
  clean, all six pyright `executionEnvironments` roots exist.
- **Only surviving `retail-demo-console`** outside `ai_checkpoints/` is the ADR-0015 problem
  statement, now `platform-console (originally retail-demo-console)` — intentional history.

## Run-mode note (not a regression)

kind publishes host **7233** (`kind-config.yaml` extraPortMapping → in-cluster Temporal
frontend) and Compose-OSS `temporal` also binds **7233**. They are **mutually exclusive** on
that port — `just up` fails with "Bind for 0.0.0.0:7233 failed" while kind is up. Pre-existing
run-mode exclusivity, surfaced here by bringing both up at once. Verified the console by
`cluster-stop` → `up` → `down` → `cluster-start`.

## Next (unchanged from 0008, plus)

- Commit + push 0009 (working tree only right now).
- Compose app-tier-only override (drop the duplicate worker fleet on the kind + Cloud path).
- ADR-0015 phase 2: `kube_status` provider → live architecture page on kind.
- ADR-0015 phase 3: topology-as-data for multi-domain.
- Wire observability onto kind (chart + scrape) — currently only proven on Compose-OSS.
- Move the app tier (orders-api, mock-api, console) onto kind; wire the in-cluster OSS
  `temporal-server` backend (the planned `kind + Local OSS` run-mode row).
