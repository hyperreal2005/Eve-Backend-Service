import uuid

from django.db import models
from django.utils import timezone


class TimeStampedModel(models.Model):
    # A plain default (not auto_now_add) keeps the field settable in tests and fixtures.
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class BaseModel(TimeStampedModel):
    """UUID primary key: ids appear in URLs, and UUIDs can't be enumerated."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True
