"""Owner-selected development bookkeeping, separate from scientific conditions."""
from datetime import datetime, timezone

ADMINISTRATIVE_BOUNDARY = "2026-11-06T00:00:00-05:00"


def development_record(now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Development timestamps require a timezone")
    boundary = datetime.fromisoformat(ADMINISTRATIVE_BOUNDARY)
    return {"phase": "pre_sprint" if now < boundary else "sprint",
            "created_at": now.isoformat(), "administrative_boundary": ADMINISTRATIVE_BOUNDARY,
            "boundary_basis": "owner-selected bookkeeping convention; not organizer-announced start",
            "parent_checkpoint": None}
