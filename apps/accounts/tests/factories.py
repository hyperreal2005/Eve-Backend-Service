from typing import Any

import factory
from factory.django import DjangoModelFactory
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import Role, User

DEFAULT_PASSWORD = "Correct-Horse-Battery-9"


class UserFactory(DjangoModelFactory):
    class Meta:
        model = User

    email = factory.Sequence(lambda n: f"user{n}@example.com")
    full_name = factory.Faker("name")
    role = Role.PATIENT

    @classmethod
    def _create(cls, model_class: type[User], *args: Any, **kwargs: Any) -> User:
        password = kwargs.pop("password", DEFAULT_PASSWORD)
        return model_class.objects.create_user(*args, password=password, **kwargs)


def bearer(user: User) -> str:
    """An `Authorization` header value carrying a fresh access token for `user`."""
    return f"Bearer {RefreshToken.for_user(user).access_token}"
