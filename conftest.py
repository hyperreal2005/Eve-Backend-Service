import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from apps.accounts.models import Role, User
from apps.accounts.tests.factories import UserFactory, bearer


@pytest.fixture(autouse=True)
def _isolated_cache():
    """Rate-limit counters live in the cache; every test starts from a clean slate."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def eager_celery():
    """Run queued tasks immediately, in-process (countdowns are ignored), then restore."""
    from config.celery import app

    previous = (app.conf.task_always_eager, app.conf.task_eager_propagates)
    app.conf.task_always_eager = app.conf.task_eager_propagates = True
    yield app
    app.conf.task_always_eager, app.conf.task_eager_propagates = previous


@pytest.fixture
def user(db) -> User:
    return UserFactory()


@pytest.fixture
def admin_user(db) -> User:
    return UserFactory(role=Role.ADMIN)


@pytest.fixture
def user_client(user: User) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=bearer(user))
    return client


@pytest.fixture
def admin_client(admin_user: User) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=bearer(admin_user))
    return client
