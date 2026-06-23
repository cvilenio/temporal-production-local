# 0003 — Get the demo working again on Docker Compose (reusable images)

- **Status:** **DONE (2026-06-23).** Restructure now on `main` (`3eb2331`). `docker compose
  up --build` on Docker Desktop built all images and the full stack came up healthy. All five
  Definition-of-done checks pass — see "Results" below.
- **Date:** 2026-06-23

## Results (verified live this session)

- **DoD 1 — images build.** All buildable services built from `images/python.Dockerfile`
  (kept option (a): two thin worker images). Added `.dockerignore` (excludes `.venv/`, VCS,
  caches, `.keys/`, `.env`). Skipped shared-base layer + explicit `image:` tags (optional).
- **DoD 2 — order completes.** `POST /submit-order` reached `completed` end-to-end. **Submit
  contract gotcha:** requires header `X-Idempotency-Key` **and** a `cart_version` =
  SHA256 of the canonical JSON (`sort_keys`, compact separators) of the body *minus*
  `cart_version` (`libs/orders/python/orders/api.py:81-94`). The console computes this in
  `apps/demo/console/python/app/routes/orders_api.py:50-58`.
- **DoD 3 — scenarios.** Batch via `POST /api/submit-batch` `{"counts":{<key>:n}}` (keys in
  `apps/demo/console/python/app/scenarios.py`):
  - `happy_path`, `inventory_flaky` (Temporal retry), `shipping_ghost` (verify-found),
    `shipping_flaky` (retry-created) → all `completed`.
  - `shipping_unrecoverable` → terminal `shipping_failed` (NOT `cancelled`), by design:
    `terminal.py:22-23` surfaces a clean `SHIPPING_FAILED` + "$10 credit" message; event
    history confirmed the saga (`release_inventory` compensation) fired after both shipment
    attempts failed verification, then WF closed COMPLETED.
  - **Batch cancel** (`/api/cancel-batch` `{"order_ids":[...]}`) → targeted in-flight orders
    reached `cancelled` (CANCELLED_BY_USER terminal).
  - **`/admin/reset`** (via console `/api/reset`) → terminated/deleted workflows + truncated
    `orders` + `idempotency_keys`; tracking cleared to 0; fresh post-reset order completed.
- **DoD 4 — console deployment features.** `/api/status/snapshot` reports all 13 services
  `healthy` with `status_source: docker` (socket mount works, not degraded to probes).
  Embedded UIs respond: console 8086, Temporal UI 8082, embedded UI proxy 8081, Grafana 3000,
  pgweb 8083/8084, orders-api 8002, mock-api 8001, codec-server 8085.
- **DoD 5 — reusability.** Two thin worker images via build args; `.dockerignore` shrinks
  context. Good enough for the demo; base-layer/image-tag optimizations deferred.

## Goal

Bring the previously-working demo back to life **on Docker Compose** (defer kind/ArgoCD).
`docker compose up --build` (`uv run poe up`) must produce a working **orders** use case
end-to-end, and the demo console's deployment-sensitive features must work. Build images
**properly and reusably** while here.

## Definition of done

1. All buildable services build from `images/python.Dockerfile`: `orders-service`,
   `orders-workflow-worker`, `orders-activity-worker`, `mock-api`, `retail-demo-console`,
   `codec-server`.
2. `docker compose up` → stack healthy; submit an order from the console (8086) → completes.
3. Demo scenarios work: Ghost / Flaky / Lost shipping, inventory-flaky retry, batch cancel,
   `/admin/reset`.
4. Console deployment-sensitive features work: live **status panel**, **log streaming**,
   embedded **Temporal UI** (8081) / **Grafana** (3000) / **pgweb** (8083).
5. Images are reusable (see "Reusability decision").

## Confirmations already done (static analysis)

- **Console status/logs survive the restructure.** `app/services/docker_status.py` keys off
  the **compose-service label** (`com.docker.compose.service`) and probes **in-network
  hostnames** (`orders-service:8000`, `mock-api:8000`, `lgtm:3000`). Compose service names +
  `container_name`s are unchanged, so mapping still works. It also **falls back to HTTP/TCP
  probes** if the container socket is unavailable. New `codec-server` simply isn't in
  `SERVICE_REGISTRY` → won't show (harmless).
- **Dockerfile path logic checks out** for every service: `COPY ${APP_PATH} ./app`, `WORKDIR
  /app/app`. Console → `/app/app/app/main.py` (uvicorn `app.main:app`) ✓; orders-api →
  `/app/app/main.py` `from orders.api import app` ✓; workers → `python main.py` ✓.
- **uv build model:** image copies root `pyproject.toml`+`uv.lock`+`libs/`, runs
  `uv sync --frozen --no-default-groups --group <APP_GROUP>`; `orders` installs editable from
  the copied `libs/orders/python`. Non-kernel groups (console/mock-api/codec) don't pull
  `orders` but the member is present so the workspace resolves.

## Runbook (next session)

1. `uv run poe up` (= `docker compose up --build`). Runtime is **Docker Desktop** — plain
   invocation, no socket remapping needed.
2. If a build fails, suspect `uv sync --frozen` lock drift (shouldn't — `uv.lock` is
   committed and fresh).
3. Bring up, watch healthchecks, then drive the console at http://localhost:8086.
4. Walk the Definition-of-done checks 2–4.

## Reusability decision (make this call while building)

- **Worker images:** `workflow` and `activity` are the *same* `workers` group + kernel, only
  the entrypoint dir differs → currently **two near-identical images**. Options:
  - (a) keep two thin-app images (simplest, matches the k8s thin-app model);
  - (b) build **one** worker image and select the profile at runtime via the kernel's own CLI
    — `python -m orders.worker --profile workflow|activity` (already supported) — one build,
    two containers. More reusable; slightly bypasses the thin per-dir mains.
  Recommend (b) for Compose reuse; keep the thin-app dirs for the k8s/versioning story.
- **Shared base layer:** consider `images/python-base.Dockerfile` (kernel installed) that app
  images build `FROM`, so the heavy `uv sync` layer is built once and cached. Optional.
- **Explicit `image:` tags** in compose so built images are named/reusable (and later
  pushable). Optional.
- Consider adding a **`.dockerignore`** (exclude `.venv/`, `.git/`, `__pycache__/`,
  `archive/`) to shrink build context.

## Risks / gotchas

- **Console socket**: mounts `/var/run/docker.sock` (Docker Desktop bridge). If unavailable,
  status panel auto-degrades to HTTP/TCP probes (still works).
- **codec-server** is independent and its codec is a placeholder (not wired into the worker
  data converter), so Event History is not actually encrypted — the codec server isn't
  required for the demo to function. Fine to leave running; can be commented out if noisy.
- **Worker versioning env is unset** in compose → workers run version-agnostic (intended;
  preserves prior behavior).

## Out of scope (later checkpoints)

kind + Terraform + ArgoCD bring-up; finishing app Helm charts; Worker Controller CRD field
confirmation; real AEAD codec; marking `OrderWorkflow` PINNED. See ADR set + checkpoint 0002.
