from django.apps import AppConfig


class CatalogConfig(AppConfig):
    name = "apps.catalog"
    label = "catalog"
    verbose_name = "Catalogue"

    def ready(self) -> None:
        from django.db.models.signals import post_delete, post_save

        from apps.catalog.cache import invalidate_on_commit
        from apps.catalog.models import DiagnosticCentre, DiagnosticTest, Offering

        # Any change to the catalogue, however it's made, retires the cached responses.
        for model in (DiagnosticCentre, DiagnosticTest, Offering):
            for signal in (post_save, post_delete):
                signal.connect(invalidate_on_commit, sender=model, dispatch_uid="catalog-cache")
