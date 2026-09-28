from rest_framework import pagination


class PageNumberPagination(pagination.PageNumberPagination):
    """For catalogue lists: small, sortable, with random access to any page."""

    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class NewestFirstCursorPagination(pagination.CursorPagination):
    """For append-mostly lists (bookings, payments): stable while new rows arrive."""

    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100
    ordering = ("-created_at", "-id")
