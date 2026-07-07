# Adapting a demo domain (Python)

This runbook ports an external Temporal demo into this repo as a first-class domain.
Java scaffolding lands in PR #2; the steps below are Python-only today.

## Before you start

Pick a short domain key (lowercase, hyphens OK) that matches the demo's Temporal namespace intent.
Read `config/domains/ziggymart.yaml` for the descriptor shape on an existing production domain.
Confirm the demo's task queues and workflow types — the verifier will fail if they drift from the kernel.

## Scaffold

Run `just scaffold-domain NAME=<domain> LANG=python` from the repo root.
The scaffolder copies `templates/domain/python/` into `libs/<domain>/` and `apps/temporal/workers/python/<domain>/`.
It writes `config/domains/<domain>.yaml`, appends the namespace to `config/temporal/namespaces.yaml`, and patches `pyproject.toml`.
It also emits a Helm chart under `deploy/charts/<domain>-workers/` and a Grafana dashboard under `compose/observability/grafana/`.
Run `uv lock` after scaffolding — new workspace members and dependency groups require a lock refresh.

## Replace the Hello stub with your demo

Edit the generated kernel under `libs/<domain>/python/<domain>/`.
Replace `HelloWorkflow`, activities, and `shared/workflow_io.py` models with your demo's workflow and activity code.
Keep `TaskQueue` constants in `shared/temporal_ids.py` — every queue name in the descriptor must match a constant here.
Wire activity routing through the template's `run_activity` helper so workflow tasks stay on the workflow queue and activities on the activity queue.
Set `VersioningBehavior.PINNED` on workflow classes that participate in Worker Deployment versioning (ADR-0004).

## Align the domain descriptor

Update `config/domains/<domain>.yaml` so `workers`, `workflows`, and `task_queue` fields mirror the kernel.
Use `kernel: <other-lib>` when the domain key differs from the Python package name (see `ziggymart` → `orders`).
Set `data_converter: default` unless you add a custom resolver in `appkit.domains.resolve_data_converter`.
The cluster layer reads this field at deploy time and injects `TEMPORAL_DATA_CONVERTER` on worker and starter pods.
Workers resolve the ref via settings at startup — unknown refs raise immediately.
Run `just verify-domains` — it must pass before you commit.

## Cloud overlay and cluster wiring

Copy an entry into `deploy/terraform/layers/cloud/terraform.tfvars` from `terraform.tfvars.example` if the scaffolder did not already append one.
Add an ArgoCD Application block in `deploy/terraform/layers/cluster/applications.tf` by copying `orders_workers_application` and tokenizing for your domain.
The cluster layer injects connection values, image digests, and `startupProbe.enabled: false` for demo domains without orders-api.
Add Grafana volume mounts in `docker-compose.yml` (copy the ziggymart dashboard/provisioning blocks).

## Build, publish, deploy

Build workflow and activity images separately — each worker profile is its own image (`APP_PATH` differs).
Push to `localhost:5001` and record digests for `terraform apply`.
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
Java domains (`templates/domain/java/`) and Spring-Boot appkit — PR #2.
Generic console trigger UI driven by domain descriptors — later milestone.

## Offline guard

`compose/scripts/tests/test_scaffold_domain.py` scaffolds into a temp tree and runs `verify-domains`.
Run `uv run pytest compose/scripts/tests/test_scaffold_domain.py` or `just test` after template changes.
