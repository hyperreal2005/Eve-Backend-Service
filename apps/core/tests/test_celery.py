from django.conf import settings

from config.celery import app


def test_every_scheduled_job_is_a_registered_task():
    app.loader.import_default_modules()
    scheduled = {entry["task"] for entry in settings.CELERY_BEAT_SCHEDULE.values()}
    assert scheduled <= set(app.tasks)
    assert {"mockpay.deliver_event", "mockpay.settle_charge"} <= set(app.tasks)


def test_tasks_are_acknowledged_only_after_they_finish():
    # With idempotent tasks, at-least-once delivery is the safe choice.
    assert app.conf.task_acks_late is True
    assert app.conf.task_reject_on_worker_lost is True
