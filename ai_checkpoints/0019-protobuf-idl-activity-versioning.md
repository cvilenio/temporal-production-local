# 0019 — Protobuf IDL for activity/workflow contracts + version-in-command

- **Status:** **LANDED + LIVE-VALIDATED ON KIND + CLOUD (2026-06-26).** lint + pyright + 13 unit
  tests + buf lint green; one happy-path order ran end-to-end on the new pinned proto build and
  COMPLETED. Decision captured in **ADR-0021**.
- **Date:** 2026-06-26
- **ADRs:** **ADR-0021** (new). Complements ADR-0004 (Worker Versioning); converter from ADR-0005
  unchanged; codegen network boundary per ADR-0013.

## Why

An internal discussion on interface compatibility surfaced the two failure classes
Temporal does *not* guard: payload-shape drift (serialization/converter failures) and
activity-semantic drift (a deploy/long run mixing activity-code versions with no record of what
ran). Pydantic `extra="forbid"` is actively *anti*-forward-compat. Adopt an IDL + version-in-command
to make these first-class, and get the polyglot contract story for free.

Three guards for three failure classes: determinism → Worker Versioning (PINNED, ADR-0004);
payload → protobuf IDL; semantic → version-in-command + activity gating.

## Done this session

- **Proto contracts** (`libs/orders/proto/`, packages `orders.activities.v1` +
  `orders.workflow.v1`): all 15 activity request/response messages + `OrderWorkflowInput/Result`.
  Convention `1=contract_version`, `2=idem_key`. Status kept as wire-string; money modeled as
  `amount_minor` (cents). `buf.yaml` (STANDARD lint) + `buf.gen.yaml` (python + pyi plugins).
- **Key finding:** `pydantic_data_converter` already carries the proto encoders ahead of its JSON
  converter → proto rides as readable `json/protobuf`, **no converter change**, proto + Pydantic
  coexist. Forces standard google `*_pb2` codegen (verified gencode 6.33 ↔ runtime 6.33.6).
- **Generated code committed** under `libs/orders/python/orders/_pb/` (ships in the wheel),
  re-exported via `orders/shared/contracts.py`. `activity_io.py`/`workflow_io.py` now thin
  re-export shims, so activity/workflow imports are unchanged.
- **version-in-command:** `orders/shared/contract_version.py` (`CONTRACT_VERSION=1`, `gate()`),
  new `ErrorType.CONTRACT_VERSION_UNSUPPORTED`. `gate(req)` at the top of all 15 activity bodies.
- **Workflow:** every request emits `contract_version`; `amount`→`amount_minor`; status fields use
  `.value`; tracks emitted versions and upserts the new **`ContractVersions`** KeywordList search
  attribute (registered in `config/temporal/namespaces.yaml`, pre-set in `services/temporal.py`).
- **Money edge conversion** in `api.py` (dollars→cents); persistence activity hand-builds the
  dollar dict for the orders-service so the HTTP/DB layer is untouched. The two other
  HTTP-forwarding activities use `MessageToDict(preserving_proto_field_name=True)`.
- **Tooling:** `just proto-gen` / `proto-lint` (in the gate) / `proto-breaking` / `proto-check`.
  `protobuf>=6.30,<7` dep added; generated dir excluded from ruff + pyright.
- **Tests:** 10 unit tests (converter encoding/round-trip, ignore-unknown, defaults-not-None,
  MessageToDict shape, gate accept/reject/widen). `poe lint` + `pyright` + `pytest` all green.

## Decisions

See **ADR-0021**. Key calls: status-as-string (no duplicated proto enum); money minor units;
commit generated code (ADR-0013 offline); buf recipes in `just` not poe; `buf breaking` excluded
from the gate until the baseline lands on main.

## Live validation (done)

- Registered `ContractVersions` on the Cloud `ziggymart` namespace (terraform cloud layer; plan was
  `1 add, 0 change, 0 destroy`). Needed a one-line module fix: the Cloud provider's enum spells the
  type `keyword_list`, so `deploy/terraform/modules/cloud-namespace/namespace.tf` now translates
  `KeywordList → keyword_list` (single-word types pass through unchanged — no churn).
- `just platform-up` rebuilt + redeployed workers + orders-api; the worker-controller promoted the
  new build to Current for both worker deployments.
- One happy-path order ($108 → 10800 cents) ran on the new **pinned** build and **COMPLETED**.
  `ContractVersions=["1"]` set. History payloads for `OrderWorkflowInput` and all activity contracts
  encode as `json/protobuf` (readable proto3 JSON). Cloud footprint = 1.

## Open questions

- None blocking. `buf breaking` gains a baseline once this merges to main.

## Next

- Open the PR (branch `protobuf-activity-contracts`) per CONTRIBUTING (rebase-merge).
