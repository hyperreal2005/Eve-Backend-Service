from dataclasses import asdict

from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenRefreshView

from apps.accounts.serializers import (
    LoginSerializer,
    RefreshTokenSerializer,
    SignupSerializer,
    TokenPairSerializer,
    UserSerializer,
)
from apps.accounts.services import auth_login, auth_logout, user_create
from apps.core.openapi import problem_responses


class _CredentialApi(APIView):
    """Public credential endpoints: no bearer token expected, rate-limited per client IP."""

    authentication_classes = ()
    permission_classes = (AllowAny,)
    throttle_classes = (ScopedRateThrottle,)


class SignupApi(_CredentialApi):
    throttle_scope = "auth_signup"

    @extend_schema(
        tags=["auth"],
        auth=[],
        summary="Create a patient account",
        request=SignupSerializer,
        responses={201: UserSerializer, **problem_responses(400, 409, 429)},
    )
    def post(self, request: Request) -> Response:
        serializer = SignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = user_create(**serializer.validated_data)
        return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)


class LoginApi(_CredentialApi):
    throttle_scope = "auth_login"

    @extend_schema(
        tags=["auth"],
        auth=[],
        summary="Exchange email and password for a token pair",
        request=LoginSerializer,
        responses={200: TokenPairSerializer, **problem_responses(400, 401, 429)},
    )
    def post(self, request: Request) -> Response:
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tokens = auth_login(**serializer.validated_data, request=request)
        return Response(asdict(tokens))


@extend_schema_view(
    post=extend_schema(
        tags=["auth"],
        summary="Rotate a refresh token",
        description="Returns a new token pair. The submitted refresh token is revoked.",
    )
)
class TokenRefreshApi(TokenRefreshView):
    pass


class LogoutApi(APIView):
    authentication_classes = ()
    permission_classes = (AllowAny,)

    @extend_schema(
        tags=["auth"],
        auth=[],
        summary="Revoke a refresh token",
        request=RefreshTokenSerializer,
        responses={204: None, **problem_responses(400, 401)},
    )
    def post(self, request: Request) -> Response:
        serializer = RefreshTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        auth_logout(refresh=serializer.validated_data["refresh"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeApi(APIView):
    @extend_schema(
        tags=["auth"],
        summary="The authenticated user",
        responses={200: UserSerializer, **problem_responses(401)},
    )
    def get(self, request: Request) -> Response:
        return Response(UserSerializer(request.user).data)
