"""The Celery application: background work (webhook delivery, hold expiry, reconciliation).

Every task is idempotent and calls a service, so a redelivered task (acks_late) or an overlapping
run is harmless. Log lines inside a task carry its id, and the id of the request that queued it.
"""

import os
from typing import Any

import structlog
from celery import Celery, Task
from celery.signals import task_postrun, task_prerun, worker_process_shutdown

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("eve_diagnostics")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@task_prerun.connect
def _bind_task_context(task_id: str | None = None, task: Task | None = None, **_: Any) -> None:
    structlog.contextvars.clear_contextvars()
    context: dict[str, Any] = {"task_id": task_id, "task": task.name if task else None}
    request_id = task.request.get("request_id") if task else None
    if request_id:
        context["request_id"] = request_id
    structlog.contextvars.bind_contextvars(**context)


@task_postrun.connect
def _clear_task_context(**_: Any) -> None:
    structlog.contextvars.clear_contextvars()


@worker_process_shutdown.connect
def _close_http_connections(**_: Any) -> None:
    from apps.mockpay.events import close_http_client  # Django is ready by the time this runs

    close_http_client()
