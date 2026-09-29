import pytest
from django.db import IntegrityError, OperationalError, transaction
from psycopg import errors as pg_errors

from apps.core.db import advisory_xact_lock, is_transient_db_error, retry_on_transient_errors


def lock_timeout() -> OperationalError:
    cause = pg_errors.LockNotAvailable("canceling statement due to lock timeout")
    error = OperationalError(str(cause))
    error.__cause__ = cause
    return error


class Flaky:
    """Fails with the given errors, then succeeds."""

    def __init__(self, *errors: Exception) -> None:
        self.errors = list(errors)
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return "done"


def test_transient_errors_are_retried_with_backoff():
    work, delays = Flaky(lock_timeout(), lock_timeout()), []
    assert retry_on_transient_errors(work, sleep=delays.append) == "done"
    assert work.calls == 3
    assert delays == [0.05, 0.1]


def test_it_gives_up_after_the_last_attempt():
    work = Flaky(lock_timeout(), lock_timeout(), lock_timeout())
    with pytest.raises(OperationalError):
        retry_on_transient_errors(work, sleep=lambda _: None)
    assert work.calls == 3


def test_other_errors_are_not_retried():
    work = Flaky(IntegrityError("duplicate key"))
    with pytest.raises(IntegrityError):
        retry_on_transient_errors(work, sleep=lambda _: None)
    assert work.calls == 1
    assert not is_transient_db_error(RuntimeError("not a database error"))


@pytest.mark.django_db
def test_no_retry_inside_an_enclosing_transaction():
    # The enclosing transaction is broken by the error, so a retry there couldn't succeed.
    work = Flaky(lock_timeout())
    with pytest.raises(OperationalError), transaction.atomic():
        retry_on_transient_errors(work, sleep=lambda _: None)
    assert work.calls == 1


@pytest.mark.django_db(transaction=True)
def test_an_advisory_lock_needs_a_transaction_to_end_with():
    with pytest.raises(RuntimeError):
        advisory_xact_lock("slot:anything")
    with transaction.atomic():
        advisory_xact_lock("slot:anything")  # released when the transaction ends
