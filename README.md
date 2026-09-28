# EVE Diagnostics — booking & payments API

A backend for booking diagnostic tests at partner centres and paying for them through a simulated
payment provider, with an idempotent, signed payment webhook.

> **Status:** work in progress. Foundations, authentication, the catalogue and bookings are
> complete; payments and the webhook follow. This README grows with each phase.

## Quick start

Requirements: Docker with Compose.

```bash
docker compose up --build
```

- Swagger UI: <http://localhost:8000/api/docs/>
- Health: <http://localhost:8000/api/v1/health/ready/>

Compose uses development-only settings, so no `.env` file is needed. Postgres is published on host
port **5433** and Redis on **6380**, to avoid clashing with locally installed services.

On start, migrations run and an idempotent demo seed creates 5 fictional centres, 14 tests,
40 priced offerings and two accounts (development only):

| Role | Email | Password |
|---|---|---|
| Administrator | `ops@eve.test` | `Ops-Console-2026!` |
| Patient | `patient@eve.test` | `Patient-Demo-2026!` |

## Local development

Requirements: Python 3.13, [uv](https://docs.astral.sh/uv/), Docker (for Postgres and Redis).

```bash
uv sync                               # install dependencies, including dev tools
cp .env.example .env                  # local settings
docker compose up -d db redis         # Postgres + Redis only
uv run python manage.py migrate
uv run python manage.py seed_demo     # optional demo data
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
- Money is an integer in paise plus a currency: `"price": 650000, "currency": "INR"` is ₹6,500.00.
- Lists are paginated (`?page=`, `?page_size=` up to 100) and sortable where noted (`?ordering=`).
- Catalogue records are never deleted: `DELETE` deactivates them, so existing bookings stay valid.
  Patients only see active records; administrators see everything and can reactivate via `PATCH`.
- Another patient's booking returns `404`, exactly like one that doesn't exist.

### Booking rules

- `appointment_at` must carry a UTC offset (`2026-10-05T10:30:00+05:30`); times without one are
  rejected rather than guessed. Responses are in UTC.
- It must be at least 60 minutes ahead and at most 30 days ahead, on a 15-minute boundary, and the
  whole slot must fall within the centre's opening hours (in the centre's time zone).
- The booking's `amount` is the offering's price at booking time; later price changes don't
  affect it.
- A new booking is `PENDING` and holds its slot until `hold_expires_at` (15 minutes) for payment.
  Submitting the same booking twice returns `409 DUPLICATE_BOOKING` naming the existing one.
- Statuses: `PENDING → CONFIRMED | FAILED | CANCELLED`, `CONFIRMED → CANCELLED` (up to 2 hours
  before the appointment). `FAILED` and `CANCELLED` are final. Status is never writable directly;
  every change is recorded in the booking's `history`.

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/api/v1/auth/signup/` | — | Create a patient account |
| POST | `/api/v1/auth/login/` | — | Email + password → access & refresh tokens |
| POST | `/api/v1/auth/token/refresh/` | refresh token | Rotate the token pair (the old refresh token is revoked) |
| POST | `/api/v1/auth/logout/` | refresh token | Revoke a refresh token |
| GET | `/api/v1/auth/me/` | bearer | The authenticated user |
| GET | `/api/v1/centres/` | — | Centres; filters `city`, `test` (id), `search`; `ordering` |
| POST | `/api/v1/centres/` | admin | Create a centre |
| GET | `/api/v1/centres/{id}/` | — | A centre with the tests it offers and their prices |
| PATCH / DELETE | `/api/v1/centres/{id}/` | admin | Update / deactivate a centre |
| GET | `/api/v1/centres/{id}/tests/` | — | Tests offered at a centre; `ordering=price` |
| POST | `/api/v1/centres/{id}/tests/` | admin | Offer a test at a price: `{"test_id", "price"}` |
| GET / PATCH / DELETE | `/api/v1/centres/{id}/tests/{test_id}/` | — / admin / admin | One offering; change its price; withdraw it |
| GET | `/api/v1/tests/` | — | The test catalogue; filters `category`, `search` |
| POST | `/api/v1/tests/` | admin | Add a test (code stored upper-case) |
| GET / PATCH / DELETE | `/api/v1/tests/{id}/` | — / admin / admin | One test; update; deactivate |
| GET | `/api/v1/tests/{id}/centres/` | — | Price comparison: centres offering it, cheapest first; filter `city` |
| POST | `/api/v1/bookings/` | bearer | Book a test: `{"centre_id", "test_id", "appointment_at"}` |
| GET | `/api/v1/bookings/` | bearer | My bookings, newest first (admins: all); filters `status`, `appointment_from`, `appointment_to` |
| GET | `/api/v1/bookings/{id}/` | owner / admin | A booking with its status history |
| POST | `/api/v1/bookings/{id}/cancel/` | owner / admin | Cancel (idempotent) |
| GET | `/api/v1/health/live/`, `/api/v1/health/ready/` | — | Liveness; readiness (database + cache) |
