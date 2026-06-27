# `apps/` — deployment units, grouped by concern

Each app here is a **deployable assembly**: it imports the domain core and the generic kit
from `libs/`, wires only the ports it uses, and starts exactly one thing. Per ADR-0022 each
app owns its composition — a standard `settings.py` (env), `dependencies.py` (the
composition root), and `main.py` (entrypoint/lifecycle), plus a `routes/` package for web
apps. The reusable code (domain definitions in `libs/orders`, the composition kit in
`libs/appkit`) lives in `libs/`; the apps are where it's assembled and shipped.

Apps are grouped one level down by **deployment class** — not by language and not by
lifecycle. The axis is *who owns it* and *is it required for the workflow to run*:

| Class | What lives here | Required to run? | Whose concern |
|---|---|---|---|
| `temporal/` | `workers/`, `codec-server/` | **Yes** — the orchestration substrate. Without the workers, no workflow executes. | Workflow authors |
| `platform/` | `console/` | No — operability tooling. The business logic runs fine without it. | Platform / SRE |
| `business/` | `orders-api/`, `mock-api/` | No (to Temporal) — domain apps and simulated integrations. Temporal-agnostic. | Product engineering |

The split mirrors three real org boundaries and keeps the question *"can this fail without
stopping the workflow?"* answerable by looking at the directory:

- **`temporal/`** is the live production path. Workers poll task queues and run workflow
  and activity code; the codec-server is the (scaffold) remote payload codec proxy. If it's
  down, work stops.
- **`platform/`** is what a platform team runs to *operate and observe* the system. Today
  that's the host-plane **`console`** — a web UI that aggregates the embedded tool UIs
  (Temporal UI, Grafana, pgweb, Headlamp, ArgoCD) and front-ends the orders demo. It's
  always-on for convenience, never on the critical path.
- **`business/`** is the company's own surface area, with no Temporal specifics. `orders-api`
  is a REST backend that happens to be an *entrypoint* into Temporal (it starts/signals
  workflows); `mock-api` is a stand-in for an external dependency the business integrates
  with. Both could be swapped for any other domain without touching `temporal/`.

> Platform concerns are, strictly, a subset of "things the business cares about." We split
> them out anyway because the operate-vs-execute-vs-domain distinction is the one that
> actually predicts ownership, blast radius, and what's safe to turn off.

## Layout

```
apps/<class>/<app>/<lang>/
```

Language sits at the leaf so a single app's polyglot implementations stay together,
mirroring `libs/<use-case>/<lang>/`. Adding a language is a new `<lang>/` dir — no reshuffle.
See [`docs/adr/0001-polyglot-shared-kernel-layout.md`](../docs/adr/0001-polyglot-shared-kernel-layout.md)
for the full rationale and [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) for the
repo-wide map.
