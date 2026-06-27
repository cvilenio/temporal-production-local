# ADR-0021: Protobuf IDL for activity/workflow contracts + version-in-command

- **Status:** Accepted
- **Date:** 2026-06-26
- **Related:** Complements ADR-0004 (Worker Versioning). The data converter wired in ADR-0005 is
  unchanged. Codegen network boundary follows ADR-0013 (air-gap tiers).

## Context

Temporal enforces one failure class hard — **determinism** (non-determinism errors on replay) —
and the docs/training lead so heavily with it that the other two interface-compatibility failure
classes are easy to lose track of. Because Temporal turns the queue into a logical element and
hides messaging behind the DataConverter, the wire is no longer in your face, so message-contract
discipline gets no automatic pressure. Two failure classes have no built-in guard:

1. **Payload compatibility** — input/output *shape* drift causing serialization/converter
   failures (Pydantic `extra="forbid"` actively *rejects* unknown fields — the opposite of
   forward-compat).
2. **Semantic compatibility** — activity *meaning* drift: a long-running execution, or a rolling
   deploy, running a mix of activity-code versions where nobody can tell what a given execution
   actually ran against.

This is "interface compatibility" (per Temporal PS guidance: have a strong opinion on an IDL, and
target a version as part of input options — a CQRS technique that suits any event-sourced system).
Determinism is unaffected by message changes; command *inputs* don't break replay. The serialization
path and the activity's internal behaviour are where these bite.

The orders domain was all Pydantic v2 models (`extra="forbid"`) over `pydantic_data_converter`,
Python-only, but the repo is explicitly polyglot-aspirational (shared-kernel layout,
language-neutral schema contracts like `libs/logging/schema/`).

## Decision

Adopt **three guards for three failure classes**, and treat them as orthogonal:

| Failure class | Guard | Where |
|---|---|---|
| Determinism | Worker Versioning (`PINNED`) | ADR-0004 |
| Payload compatibility | **Protobuf IDL** with additive evolution | this ADR |
| Semantic compatibility | **version-in-command** + activity gating | this ADR |

1. **Protobuf is the IDL for the Temporal wire contracts** — all 15 activity request/response
   messages *and* the workflow I/O (`OrderWorkflowInput`/`OrderWorkflowResult`). Sources live in
   `libs/orders/proto/` (buf module root for the orders lib), packages
   `orders.activities.v1` and `orders.workflow.v1`. Generated with **buf** (`just proto-gen`).

2. **No data-converter change.** `pydantic_data_converter` is a `CompositePayloadConverter` that
   already contains the proto encoders (`json/protobuf`, `binary/protobuf`) *ahead* of its JSON
   converter. So proto messages serialize automatically as **proto3 JSON** — human-readable in the
   Temporal UI, decodable cross-language — while everything not converted to proto (the API/DB
   layer) stays Pydantic. This also forces **standard google `*_pb2` codegen** (betterproto/
   proto-plus are not `google.protobuf.message.Message` subclasses and would fall through to the
   Pydantic converter).

3. **Field convention:** request messages use `1 = contract_version`, `2 = idem_key` (where one
   exists), business fields from `3` (or `2` for the no-idem-key persistence calls). Evolve
   **additively** — new field numbers only, never renumber/reuse. proto3 ignores unknown fields on
   decode and defaults missing ones, which *is* the forward/backward compatibility we want.

4. **version-in-command.** Every activity request carries `contract_version`. The workflow decides
   which version it *emits* (`CONTRACT_VERSION`, currently 1); each activity decides which range it
   *accepts* via `gate(req, min_v=, max_v=)` (`orders/shared/contract_version.py`). An unsupported
   version fails **non-retryably** (a version mismatch is deterministic). `0/unset` maps to v1.

5. **`ContractVersions` search attribute** (KeywordList, in `config/temporal/namespaces.yaml`):
   the workflow upserts the set of versions it has emitted, so retiring an old contract is a query
   (`ContractVersions in ("1") AND ExecutionStatus = "Running"`), not a guess. Pre-set at start to
   be queryable from the first task.

6. **Status stays a string on the wire** (existing `OrderStatus`/`OrderResultStatus` values), not a
   duplicated proto enum — keeps wire values identical and avoids maintaining a parallel enum.

7. **Money becomes integer minor units** (`amount_minor`, cents) on the contracts, removing a
   latent money-as-float bug. Conversion happens at the API edge; the orders-service HTTP/DB layer
   is untouched (the persistence activity hand-builds the dollar-denominated dict it expects).

8. **Generated code is committed** under `orders/_pb/` (ships in the wheel —
   `packages = ["orders"]`). Per ADR-0013, regeneration needs `buf` + remote plugins (Resolve/
   network tier); committing the output keeps offline Tier-1/2 rebuilds working. A networked CI
   lane runs `just proto-check` (drift) and `just proto-breaking` (wire-compat vs main). The buf
   gencode plugin is pinned to a protobuf release whose **major matches the installed runtime**
   (gencode 6.33 ↔ runtime 6.33) or import-time validation fails.

## The three version axes (team playbook)

Do not conflate these — most confusion is treating them as one.

- **Axis 1 — Worker/Workflow Versioning → determinism.** `PINNED` (ADR-0004). Versions *code* for
  replay safety. Untouched by message changes.
- **Axis 2 — proto path version (`v1` → `v2`) → hard break boundary.** For changes that can't be
  additive (remove/retype/restructure). New package + new generated class; often a new activity
  name (`capture_payment.v2`) so v1 and v2 run side by side until v1 traffic drains, then retire —
  drain decided by querying `ContractVersions` / the activity name. Rare.
- **Axis 3 — `contract_version` field → semantic compatibility.** The common case. Stay in the
  `v1` package, add a field (additive), bump `contract_version`, widen the activity's `max_v`, and
  branch on the returned version.

**Worked example.** Add currency-awareness to `capture_payment`:

*Axis 3 (additive, stay in v1):*
```proto
message CapturePaymentRequest {
  uint32 contract_version = 1;
  string idem_key         = 2;
  string auth_token       = 3;
  int64  amount_minor     = 4;
  string currency         = 5;   // ADDED; old messages decode this as "" (default)
}
```
```python
async def capture_payment(req):
    v = gate(req, min_v=1, max_v=2)             # accept both in-flight versions
    currency = req.currency if v >= 2 else "USD"
    ...
# strategy/dispatch table instead of inline when per-version logic forks:
#   _CAPTURE = {1: _capture_v1, 2: _capture_v2}; return await _CAPTURE[v](req)
```
```python
# emit-site, gated behind a patch so in-flight runs stay deterministic (axis 1):
ver = 2 if workflow.patched("capture-payment-currency") else 1
CapturePaymentRequest(contract_version=ver, ..., currency="USD" if ver >= 2 else "")
self._record_contract_version(ver)
```

*Axis 2 (breaking):* new `orders.activities.v2` package + new `_pb2` class (e.g. flat
`amount_minor` → nested `Money`), usually a new activity name, run side by side, retire v1 when
`ContractVersions` shows no live v1 traffic.

Rule of thumb: **additive → axis 3; breaking → axis 2; always patch the emit-site (axis 1).**

## Consequences

- **Gain:** language-neutral contracts (polyglot-ready), forward/backward-compatible payloads by
  construction, a queryable retirement signal, and money modeled correctly.
- **Cost:** activity I/O loses Pydantic validation/coercion and `extra="forbid"`; scalars default
  to `""`/`0` (never `None`); enums-as-string lose type safety on the wire. Mitigated by the
  `gate()` guard and keeping the API/DB edge on Pydantic.
- **New toolchain dependency:** `buf` (codegen + lint + breaking checks). Offline gate unaffected
  (generated code committed); only contract changes need the network.
