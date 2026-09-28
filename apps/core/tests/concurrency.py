"""Run the same work on several threads at once, each with its own database connection."""

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from django.db import connections


def run_concurrently[T](count: int, work: Callable[[int], T]) -> list[T]:
    """Call `work(0) … work(count - 1)` on `count` threads released at the same instant.

    Needs `@pytest.mark.django_db(transaction=True)`: the threads use separate connections, so
    they only see committed data, exactly as concurrent requests would.
    """
    barrier = threading.Barrier(count)

    def run(index: int) -> T:
        try:
            barrier.wait(timeout=10)
            return work(index)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=count) as pool:
        return list(pool.map(run, range(count)))
