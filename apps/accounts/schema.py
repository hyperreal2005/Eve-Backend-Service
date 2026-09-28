from drf_spectacular.contrib.rest_framework_simplejwt import SimpleJWTScheme


class JWTScheme(SimpleJWTScheme):
    """Documents our JWTAuthentication subclass as the bearer scheme in OpenAPI."""

    target_class = "apps.accounts.authentication.JWTAuthentication"
