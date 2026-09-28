from apps.core.errors import Conflict, DomainError


class EmailAlreadyRegistered(Conflict):
    code = "EMAIL_ALREADY_REGISTERED"
    title = "Email already registered"
    default_detail = "An account with this email already exists."


class InvalidCredentials(DomainError):
    status_code = 401
    code = "INVALID_CREDENTIALS"
    title = "Invalid credentials"
    default_detail = "The email or password is incorrect."


class InvalidRefreshToken(DomainError):
    status_code = 401
    code = "TOKEN_INVALID"
    title = "Invalid token"
    default_detail = "The refresh token is invalid, expired or revoked."
