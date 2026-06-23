# Retail Demo Console

A lightweight demo webapp for the Temporal retail demo. It allows users to trigger concurrent batches of order workflows (simulating happy paths and failures) and monitor real-time notifications via SSE from the backend database.

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
docker build -t retail-demo-console .
```

## Running

### Option A: Via Docker Compose (Recommended)

If you appended the service to the existing `docker-compose.yml`, simply run:

```bash
docker compose up -d retail-demo-console
```

### Option B: Standalone Docker Run

If running standalone, make sure to attach it to the `temporal-network` so it can resolve the database and order service by name:

```bash
docker run -p 8086:8086 \
  --network temporal-network \
  -e DATABASE_URL=postgresql://admin:password@orders-db:5432/orders_db \
  -e ORDERS_SERVICE_URL=http://orders-service:8000 \
  retail-demo-console
```

Access the UI at: http://localhost:8086

## Scenarios

The "Orders" page allows you to trigger batches of different scenarios.

| Scenario Key | Description | Stub Trigger Payload Example |
|---|---|---|
| `happy_path` | A normal successful order. | `{ "item_id": "ITEM-001", "address": "123 Main St..." }` |

