"""Helpers for interpreting PostgreSQL errors raised through Django."""

import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager

import structlog
from django.db import DatabaseError, IntegrityError, connection, transaction

log = structlog.get_logger(__name__)

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


def retry_on_transient_errors[T](
    work: Callable[[], T],
    *,
    attempts: int = 3,
    first_delay: float = 0.05,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Run `work` (a whole transaction), retrying with backoff if it hits a transient error.

    Only safe for idempotent work, and only outside any enclosing transaction: after an error the
    transaction is gone, so the retry must be able to start a fresh one.
    """
    if connection.in_atomic_block:
        return work()
    for attempt in range(1, attempts + 1):
        try:
            return work()
        except DatabaseError as exc:
            if attempt == attempts or not is_transient_db_error(exc):
                raise
            delay = first_delay * 2 ** (attempt - 1)
            log.warning(
                "db.transient_error_retrying",
                attempt=attempt,
                delay_seconds=delay,
                error=type(exc.__cause__).__name__,
            )
            sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


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
