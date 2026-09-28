"""Helpers for interpreting PostgreSQL errors raised through Django."""

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager

from django.db import DatabaseError, IntegrityError, transaction

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


@contextmanager
def translate_integrity_errors(conflicts: Mapping[str, Callable[[], Exception]]) -> Iterator[None]:
    """Run the block in a savepoint and turn known constraint violations into domain errors.

    The database, not a racy "does it exist?" pre-check, decides uniqueness. This maps its
    verdict, by constraint name, to the error the API should report. The savepoint keeps any
    enclosing transaction usable, and unknown violations propagate unchanged.
    """
    try:
        with transaction.atomic():
            yield
    except IntegrityError as exc:
        make_error = conflicts.get(constraint_name(exc) or "")
        if make_error is None:
            raise
        raise make_error() from exc
