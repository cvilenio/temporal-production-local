"""The activity contract version the workflow emits (ADR-0021, axis 3).

Deliberately dependency-free — both the workflow (emit-side) and the activities
import this plain constant. The activity-side guard that *enforces* a supported
range lives with the activities (``orders.activities.contract_gate.gate``),
because raising a Temporal ``ApplicationError`` is an activity concern, not a
shared one.

When a behaviour change ships: add the field(s) to the ``.proto`` (additive),
widen the activity's accepted range, bump ``CONTRACT_VERSION``, and gate the
emit-site behind ``workflow.patched``.
"""

# The contract version the workflow currently emits on every activity request.
CONTRACT_VERSION = 1
