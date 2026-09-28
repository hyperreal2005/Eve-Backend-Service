from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = "apps.accounts"
    label = "accounts"
    verbose_name = "Accounts"

    def ready(self) -> None:
        # Registers the OpenAPI description of our JWT authentication class.
        from apps.accounts import schema  # noqa: F401
