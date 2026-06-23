# 0003 — Get the demo working again on Docker Compose (reusable images)

- **Status:** Planned. Restructure committed (`de8162d`) + pushed to
  `origin/restructure/polyglot-k8s`. Static gate green (uv sync / ruff / format / pyright /
  import smoke). **The stack has NOT been built or run since the restructure** — that's this
  task.
- **Date:** 2026-06-23

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

1. `uv run poe up` (= `docker compose up --build`). Use the **Podman-aware** invocation —
   see memory `reference_compose_socket` (DOCKER_HOST → real TMPDIR socket) and
   `reference_container_runtime`.
2. If a build fails, suspect first: (a) BuildKit `RUN --mount=type=cache` under Podman/buildah;
   (b) `uv sync --frozen` lock drift (shouldn't — `uv.lock` is committed and fresh).
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

- **Podman**: `docker compose` may be `podman compose`; socket remap; cache-mount support.
- **Console socket**: mounts `/var/run/docker.sock`; under Podman relies on the docker.sock
  bridge. If unavailable, status panel auto-degrades to probes (still works).
- **codec-server** is independent and its codec is a placeholder (not wired into the worker
  data converter), so Event History is not actually encrypted — the codec server isn't
  required for the demo to function. Fine to leave running; can be commented out if noisy.
- **Worker versioning env is unset** in compose → workers run version-agnostic (intended;
  preserves prior behavior).

## Out of scope (later checkpoints)

kind + Terraform + ArgoCD bring-up; finishing app Helm charts; Worker Controller CRD field
confirmation; real AEAD codec; marking `OrderWorkflow` PINNED. See ADR set + checkpoint 0002.
