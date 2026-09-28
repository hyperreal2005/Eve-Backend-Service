# EVE Diagnostics — booking & payments API

A backend for booking diagnostic tests at partner centres and paying for them through a simulated
payment provider, with an idempotent, signed payment webhook.

> **Status:** feature-complete (authentication, catalogue, bookings, payments, the idempotent
> webhook and background jobs). Final documentation follows.

## Quick start

Requirements: Docker with Compose.

```bash
docker compose up --build
```

- Swagger UI: <http://localhost:8000/api/docs/>
- Health: <http://localhost:8000/api/v1/health/ready/>
- Services: `api` (gunicorn), `worker` and `beat` (Celery), `db` (PostgreSQL 18), `redis`
  (cache, rate limits, task broker), plus a one-shot `migrate` that also seeds demo data.

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

### Payments

Payments go through **MockPay**, a simulated provider behind the same interface a real gateway
(e.g. Razorpay) would implement. Test payment methods choose the outcome, like a provider's test
cards:

| `payment_method` | Response | Result |
|---|---|---|
| `mock_success` | `201` | Payment `SUCCESS`, booking `CONFIRMED` |
| `mock_decline`, `mock_insufficient_funds` | `201` | Payment `FAILED` with a `failure_code`; booking `FAILED` (book again to retry) |
| `mock_async_success`, `mock_async_failure` | `202` | Payment `PENDING`; the result arrives by webhook (UPI-collect style) |
| `mock_gateway_error` | `202` | The provider charged but the response was lost: `PENDING` until the webhook reports it |
| `mock_random` (default) | `201` | Success 80% of the time |

- The amount is always the booking's snapshot; the client can't send one.
- Only the booking's patient can pay. A booking has at most one payment in flight or succeeded
  (database-enforced), so a double click can't charge twice.
- An unpaid booking whose hold has expired can't be paid: it becomes `FAILED` (`PAYMENT_TIMEOUT`)
  and the request gets `409 BOOKING_EXPIRED`.
- Cancelling is refused while a payment is in flight (`409 PAYMENT_IN_PROGRESS`). Cancelling a
  paid booking marks the payment `refund_status: PENDING` (a refund is owed; processing refunds is
  out of scope).

### The webhook

`POST /api/v1/payments/webhook/` receives provider events signed per the
[Standard Webhooks](https://github.com/standard-webhooks/standard-webhooks/blob/main/spec/standard-webhooks.md)
spec: `webhook-id`, `webhook-timestamp` and `webhook-signature` (`v1,` + base64
HMAC-SHA256 over `{id}.{timestamp}.{raw body}`), with a 5-minute replay window.

```json
{"type": "payment.succeeded", "timestamp": "2026-10-05T04:30:00Z",
 "data": {"payment_id": "…", "provider_payment_id": "mockpay_pay_…", "amount": 35000, "currency": "INR"}}
```

Send a correctly signed event without computing an HMAC yourself:

```bash
docker compose exec api python manage.py send_webhook <payment_id>            # the charge's result
docker compose exec api python manage.py send_webhook <payment_id> --times 3  # redelivered 3 times
docker compose exec api python manage.py send_webhook <payment_id> --status failed --amount 1
docker compose exec api python manage.py send_webhook <payment_id> --bad-signature
```

| Request | Response | Effect |
|---|---|---|
| Unsigned, badly signed or older than 5 minutes | `401` | Nothing stored (it may be forged or replayed) |
| Signed but malformed | `400` | Nothing stored |
| A new event | `200 processed` | Applied: `applied`, or `noop` if the result was already known |
| The same `webhook-id` again (even concurrently) | `200 duplicate` | Nothing changes |
| `payment.failed` after the payment succeeded | `200 ignored` | Stale; the captured payment stands |
| Success for a booking that already failed or was cancelled | `200 processed`, `refund_owed` | Payment recorded as `SUCCESS`, refund flagged, booking unchanged |
| Unknown payment, or an amount/currency that doesn't match | `200 rejected` | Stored for review, nothing applied |
| A transient database failure | `503` | Nothing stored; the provider's retry succeeds |

Genuine events always get a `200`, even ones we can't apply: retrying can't fix them, and payment
providers disable endpoints that keep failing. Recording an event and applying it happen in one
database transaction, so an event can never be recorded without its effect or applied twice.

### Background jobs (Celery)

The stack runs a Celery `worker` and `beat`. No request depends on them for correctness: a lapsed
hold is refused at payment time anyway, and an unknown payment outcome stays `PENDING` rather
than being guessed. They keep state tidy and make the provider behave like a real one.

| Job | When | What it does |
|---|---|---|
| MockPay webhook delivery | After each charge result | Delivers the signed event at least once: retries 5xx, 408, 429 and network errors with exponential backoff and jitter (up to 8 attempts), signs each attempt afresh, and delivers ~20% of events twice on purpose |
| MockPay settlement | ~10 s after an async charge | Settles `mock_async_*` and `mock_gateway_error` charges, then announces them by webhook |
| Hold sweeper | Every 60 s | Fails unpaid bookings whose hold lapsed (`PAYMENT_TIMEOUT`), using `SELECT … FOR UPDATE SKIP LOCKED`. Bookings with a payment in flight are left alone |
| Reconciliation | Every 60 s | Asks the provider about payments `PENDING` for over 2 minutes and applies its answer. "No such charge" counts as `FAILED` |

Tasks are acknowledged only after they finish (`acks_late`), so a crashed worker's task runs
again; every task is idempotent, which makes that safe. One `request_id` traces an asynchronous
payment through the API request, the settlement task and the webhook delivery.

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
| POST | `/api/v1/payments/` (also `/payments/`) | owner | Pay for a booking: `{"booking_id", "payment_method"}` |
| GET | `/api/v1/payments/{id}/` | owner / admin | A payment's status |
| POST | `/api/v1/payments/webhook/` (also `/payments/webhook/`) | provider signature | Payment-provider events |
| GET | `/api/v1/health/live/`, `/api/v1/health/ready/` | — | Liveness; readiness (database + cache) |
