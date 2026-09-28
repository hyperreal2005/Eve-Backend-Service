from django.urls import path

from apps.accounts.apis import LoginApi, LogoutApi, MeApi, SignupApi, TokenRefreshApi

app_name = "accounts"

urlpatterns = [
    path("signup/", SignupApi.as_view(), name="signup"),
    path("login/", LoginApi.as_view(), name="login"),
    path("token/refresh/", TokenRefreshApi.as_view(), name="token-refresh"),
    path("logout/", LogoutApi.as_view(), name="logout"),
    path("me/", MeApi.as_view(), name="me"),
]
