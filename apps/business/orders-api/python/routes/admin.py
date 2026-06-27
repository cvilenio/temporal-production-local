"""Admin / demo-reset route — destructive, gated behind DEMO_RESET_ENABLED."""

from composition import get_db_session, get_temporal_service
from fastapi import APIRouter, Depends, HTTPException
from orders.services.temporal import TemporalService
from settings import settings
from sqlalchemy import text

router = APIRouter()

# App tables truncated on reset. Listed explicitly (not reflected) so a future
# table isn't silently wiped without a deliberate edit here.
_RESET_TABLES = ("orders", "idempotency_keys")


@router.post("/admin/reset")
async def admin_reset(
    delete_closed: bool = True,
    local_only: bool = False,
    temporal_service: TemporalService = Depends(get_temporal_service),
    session=Depends(get_db_session),
):
    """Reset the demo to a clean slate: terminate (and optionally delete) all
    workflows in the namespace, then truncate the app's order tables.

    Destructive and irreversible — gated behind DEMO_RESET_ENABLED. The Temporal
    pass runs first; if it raises (e.g. cluster unreachable) the DB is left
    untouched and the caller gets a 5xx rather than a half-reset.

    `local_only` skips the Temporal pass entirely and truncates only the local
    order tables. This is the safe scope against a managed/shared **Temporal Cloud**
    namespace, where the console must never terminate or delete workflows it doesn't
    own. The console sets it on the cloud backend (see ADR-0015 / the reset modal).
    """
    if not settings.demo_reset_enabled:
        raise HTTPException(
            status_code=403,
            detail="Reset disabled. Set DEMO_RESET_ENABLED=true to enable.",
        )

    # 1. Temporal namespace — terminate open, optionally delete closed. Skipped
    #    entirely on the local-only (Cloud-safe) path.
    workflows = None
    if not local_only:
        workflows = await temporal_service.reset_workflows(delete_closed=delete_closed)

    # 2. App database — wipe orders + idempotency cache. TRUNCATE both in one
    #    statement so there's no window where one is cleared and the other isn't.
    await session.execute(text(f"TRUNCATE TABLE {', '.join(_RESET_TABLES)}"))
    await session.commit()

    return {
        "ok": True,
        "local_only": local_only,
        "workflows": workflows,
        "database": {"truncated": list(_RESET_TABLES)},
    }
