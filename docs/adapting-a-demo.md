# Adapting a demo domain

This runbook ports an external Temporal demo into this repo as a first-class domain.
Use `LANG=python` or `LANG=java` with the scaffolder — both share the same descriptor
and `verify-domains` contract (ADR-0026).

## Before you start

Pick a short domain key (lowercase, hyphens OK) that matches the demo's Temporal namespace intent.
Read `config/domains/ziggymart.yaml` for the descriptor shape on an existing production domain.
Confirm the demo's task queues and workflow types — the verifier will fail if they drift from the kernel.

## Scaffold

Run `just scaffold-domain NAME=<domain> LANG=<python|java>` from the repo root.

**Python** copies `templates/domain/python/` into `libs/<domain>/python/` and
`apps/temporal/workers/python/<domain>/`, patches `pyproject.toml`, and requires `uv lock`
after scaffolding.

**Java** copies `templates/domain/java/` into `libs/<domain>/java/` and
`apps/temporal/workers/java/<domain>/`, patches `settings.gradle`, and uses Gradle
(`just java-build`) for compile checks.

Both languages write `config/domains/<domain>.yaml`, append the namespace to
`config/temporal/namespaces.yaml`, emit a Helm chart under `deploy/charts/<domain>-workers/`,
and a Grafana dashboard under `compose/observability/grafana/`.

## Replace the Hello stub with your demo

### Python

Edit the generated kernel under `libs/<domain>/python/<domain>/`.
Replace `HelloWorkflow`, activities, and `shared/workflow_io.py` models with your demo's workflow and activity code.
Keep `TaskQueue` constants in `shared/temporal_ids.py` — every queue name in the descriptor must match a constant here.
Wire activity routing through the template's `run_activity` helper so workflow tasks stay on the workflow queue and activities on the activity queue.
Set `VersioningBehavior.PINNED` on workflow classes that participate in Worker Deployment versioning (ADR-0004).

### Java

Edit the generated kernel under `libs/<domain>/java/` (interfaces + shared types) and worker modules under
`apps/temporal/workers/java/<domain>/` (workflow impl in `.workflow`, activity bean in `.activity`).
Keep queue constants in `shared/TemporalIds.java` — verify-domains reads `*Ids.java` under the Java kernel.
Route activities with `ActivityOptions.setTaskQueue(TemporalIds.ACTIVITY_TASK_QUEUE)` on the activity stub
(in addition to `@ActivityImpl(taskQueues = ...)` on the bean).
Use `@WorkflowVersioningBehavior(PINNED)` on workflow impls that participate in Worker Deployment versioning (ADR-0004).
Keep workflow inputs simple (e.g. a string field) until cross-language interop is explicitly tested — the Phase B
console will encode inputs from `sample_inputs` in the domain descriptor.

## Align the domain descriptor

Update `config/domains/<domain>.yaml` so `workers`, `workflows`, and `task_queue` fields mirror the kernel.
Use `kernel: <other-lib>` when the domain key differs from the library package name (see `ziggymart` → `orders`).
Set `data_converter: default` unless you add a custom resolver (Python: `appkit.domains.resolve_data_converter`;
Java: `@Primary @Bean(name = "mainDataConverter")` per samples-java `payloadconverter/*`).
The cluster layer reads `data_converter` at deploy time and injects `TEMPORAL_DATA_CONVERTER` on worker pods.
Workers resolve the ref at startup — unknown refs raise immediately.
Run `just verify-domains` — it must pass before you commit.

## Cloud overlay and cluster wiring

Copy an entry into `deploy/terraform/layers/cloud/terraform.tfvars` from `terraform.tfvars.example` if the scaffolder did not already append one.
Add an ArgoCD Application block in `deploy/terraform/layers/cluster/applications.tf` by copying `orders_workers_application` and tokenizing for your domain.
The cluster layer injects connection values, image digests, and `startupProbe.enabled: false` for demo domains without orders-api.
Add Grafana volume mounts in `docker-compose.yml` (copy the ziggymart dashboard/provisioning blocks).

## Build, publish, deploy

Build workflow and activity images separately — each worker profile is its own image.

**Python** — `images/python.Dockerfile` with `APP_PATH` per profile; push to `localhost:5001`.

**Java** — `images/java.Dockerfile` with `DOMAIN`, `APP_MODULE`, `WORKER_REL_PATH`, and `APP_JAR` build args.
Java charts omit a container `command` so the image `ENTRYPOINT` applies `JAVA_OPTS` (including
`-XX:MaxRAMPercentage=75.0`). Push both profiles to `localhost:5001`.

Bump the chart `version` in `deploy/charts/<domain>-workers/Chart.yaml` and the matching `*_workers_chart_version` default in cluster `variables.tf`.
Run `just chart-publish` and confirm the OCI chart landed before any `terraform apply` (never chain publish && apply).
Apply the cluster layer with the new chart version and worker image digests.
Prefer a surgical redeploy over full `just platform-up` when only one domain changed (see `docs/RUNMODES.md`).

## Verify live (minimum footprint)

Ensure `just preflight` passes (platform-console up before kind mutations).
On kind+OSS or kind+Cloud, confirm both task queues appear in `DescribeTaskQueue`.
Start exactly one workflow execution to prove end-to-end routing — reuse that execution for follow-on checks.
Confirm activity tasks land on `<domain>-activity-task-queue`, not the workflow queue.
Open the generated Grafana dashboard — panels must use datasource uid `prometheus-kind` and `namespace="<domain>"` labels.
Terminate or cancel the test execution when done.

## Observability checklist

SDK metrics label Temporal namespace as the bare domain name (`hello`, not `hello.<account>`).
Dashboard PromQL must reference `task_queue="<domain>-workflow-task-queue"` and the activity queue separately.
Schedule-to-start panels show NaN when idle — that is expected with no backlog.

## What the scaffolder does not do yet

ArgoCD Application wiring, docker-compose Grafana mounts, and Cloud namespace bootstrap beyond the tfvars stub.
Generic console trigger UI driven by domain descriptors — Phase B (PR #3).

## Offline guard

`compose/scripts/tests/test_scaffold_domain.py` scaffolds into a temp tree (Python and Java) and runs `verify-domains`.
Run `uv run pytest compose/scripts/tests/test_scaffold_domain.py` or `just test` after template changes.
