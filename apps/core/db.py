"""Helpers for interpreting PostgreSQL errors raised through Django."""

from django.db import DatabaseError, IntegrityError

# serialization_failure, deadlock_detected, lock_not_available, query_canceled (statement_timeout)
TRANSIENT_SQLSTATES = frozenset({"40001", "40P01", "55P03", "57014"})


def constraint_name(exc: IntegrityError) -> str | None:
    """Name of the constraint that raised `exc`, so a service can map it to a domain error."""
    diag = getattr(exc.__cause__, "diag", None)
    return getattr(diag, "constraint_name", None)


def is_transient_db_error(exc: BaseException) -> bool:
    """True for failures a retry can fix: lock or statement timeouts, deadlocks, serialisation."""
    if not isinstance(exc, DatabaseError):
        return False
    return getattr(exc.__cause__, "sqlstate", None) in TRANSIENT_SQLSTATES
