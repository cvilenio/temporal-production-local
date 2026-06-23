# Temporal Retail Demo

This repository contains a full end-to-end demonstration of a retail order processing workflow orchestrated by Temporal, along with a mock external API and a real-time console.

It is designed to showcase how Temporal solves the problem of **ambiguous side-effects** (e.g. an external API times out, but you aren't sure if it succeeded), **saga compensation**, **signal-driven cancellation**, and **dead-letter handling**.

> **Layout & design:** this README documents the demo's behavior. For the repository
> structure (shared-kernel polyglot monorepo), the kind + Terraform + ArgoCD lifecycle,
> worker versioning, and the Temporal Cloud switch, see
> [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and the ADRs in [`docs/adr/`](docs/adr/).
> Apps live under `apps/` (grouped by class: `temporal/`, `business/`, `demo/`), shared
> code under `libs/`, deployment under `deploy/`.

## Architecture

![Architecture](https://via.placeholder.com/800x400.png?text=Retail+Demo+Architecture)

### Order ID Model

This demo uses a single customer-friendly order ID that replaces both a UUIDv7 internal key and a separate display ID:

1. **Order ID:** Format `ORD-{16 Crockford Base32 chars}` (e.g. `ORD-01K8XYZ1A2B3C4D5`), 20 characters total.
   - **Time-sortable:** First 10 chars encode a 48-bit millisecond timestamp (lexical sort = chronological sort).
   - **Customer-friendly:** Crockford Base32 excludes ambiguous characters (`I`, `L`, `O`, `U`) so `0` and `O` can't be confused when read aloud to a support rep.
   - **Collision-resistant:** Final 6 chars encode a 32-bit random value (~4.3B unique IDs per millisecond).
   - Used directly as both the Postgres primary key and the Temporal search attribute (`OrderId`).
2. **Workflow ID:** A separate column in the `orders` table that stores the Temporal workflow handle. Today it mirrors the order ID, but maintaining it as a distinct field leaves room to change the ID strategy later without touching the workflow lookup path.
3. **Trace ID:** A W3C-compliant 32-character hex string representing the distributed trace, stored in Postgres and mapped to the `TraceId` search attribute in Temporal.

The system consists of five primary components:
1. **Orders Service:** A FastAPI application exposing endpoints to start, track, and cancel orders.
2. **Workflow Worker:** A dedicated worker polling `orders-workflow-task-queue`. It hosts the deterministic `OrderWorkflow` orchestration logic.
3. **Activity Worker:** A dedicated worker polling `orders-activity-task-queue`. It hosts all non-deterministic side-effects (API calls, DB persistence).
4. **Mock API:** A standalone service simulating external systems (Payment, Inventory, Shipping). It enforces idempotency via headers and conditionally injects latency to simulate failures.
5. **Retail Demo Console:** A real-time UI that allows you to submit batches of orders and monitor their progress via SSE (Server-Sent Events) fed directly from the business database.
6. **Postgres (Orders DB):** Stores the actual `orders` table. The activities transactionally write to this database.

### Worker Topology & Best Practices

This demo illustrates the best practice of separating **Workflows** and **Activities** into dedicated worker fleets. While they share the same codebase and container image, they are started with different roles:

- **Workflow Worker (`--role workflow`):** CPU-bound, handles orchestration. It benefits from "Sticky Execution" (caching workflow state in memory).
- **Activity Worker (`--role activity`):** IO-bound, handles side-effects. It can be scaled independently to handle heavy external API traffic without starving the workflow orchestrator.

These workers communicate via separate Task Queues (`orders-workflow-task-queue` and `orders-activity-task-queue`), allowing for precise task routing and isolation.

### The "Three-Layer Write" Pattern
This demo explicitly highlights a production-grade activity pattern. For every logical step (e.g. "Create Shipment"), the workflow executes three distinct activities:
1. `create_shipment` - External call to the mock API.
2. `persist_shipment` - Local database update to Postgres.
3. `update_customer_status` - Atomically updates the order's status and customer-facing message.

Each of these has its own independently tuned Retry Policy.

## Prerequisites
- Docker & Docker Compose
- `uv` (optional, for local Python development)

## Running the Demo

Start the entire stack using Docker Compose:

```bash
docker compose up -d
```

### Available Services

| Service | URL | Purpose |
|---|---|---|
| **Demo Console** | http://localhost:8086 | The main operator UI. Trigger orders and watch real-time status. |
| **Temporal UI** | http://localhost:8082 | View the workflow execution history and event logs. |
| **pgweb (Orders)** | http://localhost:8083 | Inspect the `orders` table in Postgres. |
| **pgweb (Temporal)** | http://localhost:8084 | Inspect Temporal's internal backing store. |

## Operator Observability

This demo uses **Temporal Search Attributes** to surface business state in the platform UI.

- **OrderId**: The order ID (e.g. `ORD-01K8XYZ1A2B3C4D5`).
- **OrderStatus**: The current lifecycle status (e.g. `inventory_reserved`, `cancelled_with_issues`).
- **TraceId**: The W3C trace ID for end-to-end tracing.

Operators can filter workflows in the Temporal Web UI using these attributes (e.g., `OrderStatus = "cancelled_with_issues"`) to identify workflows requiring manual intervention.

## Demo Scenarios

From the **Demo Console (http://localhost:8086)**, navigate to the Orders page.

### Happy Path
1. Select **"Happy Path"** and click **"Trigger scenarios"**. The order will complete in milliseconds.

### Shipping Response Lost (Ghost Label)
2. Select **"Shipping Response Lost"** and click **"Trigger scenarios"**.
   - The mock API hangs on the first call, exceeding Temporal's `start_to_close_timeout` (3s).
   - The label was actually created in the background, so the workflow's read-after-write verification finds it and recovers.

### Shipping Timeout Recovered on Retry (Flaky API)
3. Select **"Shipping Timeout Recovered on Retry"** and click **"Trigger scenarios"**.
   - The mock API hangs on the first call, causing a Temporal timeout.
   - Verification finds no label, so the workflow explicitly retries the create activity.
   - The second attempt succeeds and the workflow continues.

### Shipping Unrecoverable (Lost Label)
4. Select **"Shipping Timeout Unrecoverable"** and click **"Trigger scenarios"**.
   - The mock API hangs twice. After two cycles of timeout + verify (not_found), the workflow gives up.
   - The workflow runs **saga compensation**: it calls `release_inventory` to undo the reservation.
   - The order transitions to `shipping_failed`.

### Inventory Flaky (Temporal Retry Policy)
5. Select **"Inventory Flaky (Temporal Retry)"** and click **"Trigger scenarios"**.
   - The inventory service returns transient 503 errors on the first two attempts.
   - Temporal's **Retry Policy** automatically handles these retries under the hood (visible in the History UI).
   - The third attempt succeeds without the workflow being aware of the flakiness.

### Batch Cancellation
6. Submit any mixed batch, then on the **Tracking** page click **"Cancel All In-Flight"** on a batch.
   - The backend sends a `cancel_order` signal to every in-flight workflow.
   - Each signalled workflow runs its saga compensation stack and transitions to `cancelled`.

## Retry Patterns in This Workflow

This demo showcases two distinct ways to handle retries:

1. **Temporal Retry Policy:** Used where the external API is **idempotent** (like our Payment and Inventory mocks). If an activity fails with a retryable error, Temporal handles the backoff and retry transparently.
2. **Workflow-Level Explicit Retries:** Used where the external API is **non-idempotent** or brittle (like our Shipping mock). To prevent duplicate side-effects (e.g., double-charging or double-shipping), the workflow follows a "Write-then-Verify" pattern, manually checking the status before deciding whether it is safe to retry the write.

## Failure Injection

The mock API uses deterministic payload magic strings to trigger failures:

| Trigger | Where | Failure Mode |
|---|---|---|
| `Ghost` in address | `POST /shipping/request` | Hang once (write lands), then succeed (read-after-write demo) |
| `Flaky` in address | `POST /shipping/request` | Hang once (no write), then succeed on second create attempt |
| `Lost` in address | `POST /shipping/request` | Hang always (no write, triggers saga after 2 cycles) |
| `Flaky` in item_id | `POST /inventory/reserve` | Return 503 twice, then 200 (retry policy demo) |

## Troubleshooting

If you are upgrading from a previous version, wipe your local volumes:

```bash
docker compose down -v
```

## What is excluded?
- **External Pub-Sub:** Notifications use direct Postgres writes for demo simplicity.

A **Codec Server** scaffold is now included (`apps/codec-server/`, ADR-0006) — replace its
placeholder codec with AEAD before any real use.
