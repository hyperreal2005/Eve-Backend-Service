import pytest

from apps.mockpay.events import close_http_client


@pytest.fixture(autouse=True)
def _close_pooled_http_connections():
    """A kept-alive connection would pin a live-server thread, and its database session."""
    yield
    close_http_client()
