# Platform Console

A lightweight host-plane webapp for managing and observing the local Temporal platform across business domains. It aggregates the embedded tool UIs (Temporal UI, Grafana, pgweb, Headlamp, ArgoCD) and, for the orders demo workload, lets users trigger concurrent batches of order workflows (happy paths and failures) and monitor real-time notifications via SSE from the backend database.

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | _required_ | Postgres connection string (e.g. `postgresql://user:pass@postgres:5432/app`) |
| `ORDERS_SERVICE_URL` | `http://orders-service:8000` | Base URL of the orders service |
| `PORT` | `8086` | Public port |
| `LOG_BUFFER_SIZE` | `500` | Max entries kept in the in-memory submission log ring buffer |
| `MESSAGE_POLL_INTERVAL_SECONDS` | `3` | How often the SSE background task polls Postgres for new notifications |

## Building

To build the Docker container:

```bash
docker build -t platform-console .
```

## Running

### Option A: Via Docker Compose (Recommended)

If you appended the service to the existing `docker-compose.yml`, simply run:

```bash
docker compose up -d platform-console
```

### Option B: Standalone Docker Run

If running standalone, make sure to attach it to the `temporal-network` so it can resolve the database and order service by name:

```bash
docker run -p 8086:8086 \
  --network temporal-network \
  -e DATABASE_URL=postgresql://admin:password@orders-db:5432/orders_db \
  -e ORDERS_SERVICE_URL=http://orders-service:8000 \
  platform-console
```

Access the UI at: http://localhost:8086

## Scenarios

The "Orders" page allows you to trigger batches of different scenarios.

| Scenario Key | Description | Stub Trigger Payload Example |
|---|---|---|
| `happy_path` | A normal successful order. | `{ "item_id": "ITEM-001", "address": "123 Main St..." }` |

