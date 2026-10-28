"""Append-only audit log: who viewed or changed what.

Record ids and actions only. Never put contribution text, notes, grades or
personal details in ``details``; the log must stay safe to show an auditor.
"""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from parti.models import AuditEvent, User

_FORBIDDEN_DETAIL_KEYS = frozenset({"summary", "body", "note", "value", "grade", "email", "name", "text"})


def record(
    db: Session,
    action: str,
    entity_type: str,
    entity_id: str | uuid.UUID | int | None = None,
    *,
    actor: User | None = None,
    course_id: uuid.UUID | None = None,
    **details: Any,
) -> AuditEvent:
    """Add an audit event to the current transaction. The caller commits."""
    leaked = _FORBIDDEN_DETAIL_KEYS.intersection(details)
    if leaked:
        raise ValueError(f"Audit details must not contain content fields: {sorted(leaked)}")
    event = AuditEvent(
        actor_id=actor.id if actor else None,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        course_id=course_id,
        details=details,
    )
    db.add(event)
    return event
