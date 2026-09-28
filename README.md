# EVE Diagnostics — booking & payments API

A backend for booking diagnostic tests at partner centres and paying for them through a simulated
payment provider, with an idempotent, signed payment webhook.

> **Status:** work in progress. Foundations and authentication are complete; the catalogue,
> bookings, payments and webhook follow. This README grows with each phase.

## Quick start

Requirements: Docker with Compose.

```bash
docker compose up --build
```

- Swagger UI: <http://localhost:8000/api/docs/>
- Health: <http://localhost:8000/api/v1/health/ready/>

Compose uses development-only settings, so no `.env` file is needed. Postgres is published on host
port **5433** and Redis on **6380**, to avoid clashing with locally installed services.

## Local development

Requirements: Python 3.13, [uv](https://docs.astral.sh/uv/), Docker (for Postgres and Redis).

```bash
uv sync                               # install dependencies, including dev tools
cp .env.example .env                  # local settings
docker compose up -d db redis         # Postgres + Redis only
uv run python manage.py migrate
uv run python manage.py runserver     # http://localhost:8000/api/docs/
```

## Tests and quality checks

```bash
docker compose up -d db               # tests run against real PostgreSQL, never SQLite
uv run pytest --cov
uv run ruff check . && uv run ruff format --check .
uv run python manage.py makemigrations --check --dry-run
```

The suite runs on PostgreSQL because the design relies on Postgres guarantees: partial unique
indexes, check constraints and row locks.

## API conventions

- Versioned under `/api/v1/`. Trailing slashes are optional; nothing is ever redirected.
- Every error is an [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) `application/problem+json`
  body with a stable, machine-readable `code` and the request's `request_id` (also sent as the
  `X-Request-ID` header).
- Authentication: `Authorization: Bearer <access token>` from `POST /api/v1/auth/login/`.

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/api/v1/auth/signup/` | — | Create a patient account |
| POST | `/api/v1/auth/login/` | — | Email + password → access & refresh tokens |
| POST | `/api/v1/auth/token/refresh/` | refresh token | Rotate the token pair (the old refresh token is revoked) |
| POST | `/api/v1/auth/logout/` | refresh token | Revoke a refresh token |
| GET | `/api/v1/auth/me/` | bearer | The authenticated user |
| GET | `/api/v1/health/live/`, `/api/v1/health/ready/` | — | Liveness; readiness (database + cache) |
