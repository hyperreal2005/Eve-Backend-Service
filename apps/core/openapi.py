from http import HTTPStatus
from typing import Any

from drf_spectacular.utils import OpenApiResponse

from apps.core.serializers import ProblemDetailSerializer


def only_versioned_endpoints(endpoints: list[tuple[Any, ...]], **_: Any) -> list[tuple[Any, ...]]:
    """drf-spectacular hook: document only the canonical /api/v1/ routes.

    The unversioned aliases for the brief's literal paths share the same views, so documenting
    them would publish the same operation twice.
    """
    return [endpoint for endpoint in endpoints if endpoint[0].startswith("/api/v1/")]


def problem_responses(*statuses: int) -> dict[int, OpenApiResponse]:
    """OpenAPI entries for the given error statuses, all described by the problem+json schema."""
    return {
        status: OpenApiResponse(ProblemDetailSerializer, description=HTTPStatus(status).phrase)
        for status in statuses
    }
