# EVE Diagnostics — booking & payments API

A Django REST API for booking diagnostic tests at partner centres and paying for them through a
simulated payment provider, with an idempotent, signed payment webhook.

- **The database enforces the rules** that matter: check constraints and partial unique indexes
  make double bookings, double charges and invalid states impossible, even under concurrency.
- **One pure function decides what every payment result means.** The provider's immediate
  response, its webhook and the reconciliation job all apply results through it, so they agree
  whatever order they arrive in.
- **The webhook** verifies [Standard Webhooks](https://github.com/standard-webhooks/standard-webhooks/blob/main/spec/standard-webhooks.md)
  signatures with a replay window, and records and applies each event in one transaction.
  Eight identical deliveries sent at the same instant are applied exactly once (tested).
- **A realistic simulated provider** (MockPay) with its own ledger, test payment methods, and
  at-least-once webhook delivery with retries, backoff and deliberate duplicates.
- **Slot capacity** (an MRI scanner takes one patient per slot) enforced under a per-slot lock,
  with an availability endpoint. **`Idempotency-Key`** support makes client retries safe, and a
  **versioned Redis cache** serves the public catalogue.
- Every error is an RFC 9457 `application/problem+json` body with a stable `code`.
  462 tests at 98% coverage run against real PostgreSQL.

**Contents:** [Quick start](#quick-start) ·
[Walkthrough](#walkthrough) · [API reference](#api-reference) · [How it works](#how-it-works) ·
[Database design](#database-design) · [Edge cases](#edge-cases) ·
[Tests](#tests-and-quality-checks) · [Configuration](#configuration) ·
[Assumptions](#assumptions) · [Improvements](#what-id-improve-with-more-time) ·
[Project layout](#project-layout)

---

## Quick start

Requirements: Docker with Compose.

```bash
docker compose up --build
```

- Swagger UI: <http://localhost:8000/api/docs/> (use **Authorize** with an access token)
- Health: <http://localhost:8000/api/v1/health/ready/>
- Admin site (read-only for bookings, payments and webhook events): <http://localhost:8000/admin/>

Compose runs `api` (gunicorn), `worker` and `beat` (Celery), `db` (PostgreSQL 18) and `redis`
(cache, rate limits and task broker), plus a one-shot `migrate` that applies migrations and seeds
demo data: 5 fictional centres, 14 tests, 40 priced offerings (scans like MRI take one patient
per slot) and two accounts. It uses
development-only settings, so no `.env` file is needed. Postgres is published on host port
**5433** and Redis on **6380**, to avoid clashing with locally installed services.

| Demo account | Email | Password |
|---|---|---|
| Administrator | `ops@eve.test` | `Ops-Console-2026!` |
| Patient | `patient@eve.test` | `Patient-Demo-2026!` |

### Local development (without Docker for the app)

Requirements: Python 3.13, [uv](https://docs.astral.sh/uv/), and Docker for Postgres and Redis.

```bash
uv sync                               # dependencies, including dev tools
cp .env.example .env                  # local settings
docker compose up -d db redis         # Postgres + Redis only
uv run python manage.py migrate
uv run python manage.py seed_demo     # optional demo data
uv run python manage.py runserver     # http://localhost:8000/api/docs/
uv run celery -A config worker -B -l INFO   # optional: MockPay webhooks and periodic jobs
                                            # (on Windows, add --pool solo)
```

On macOS/Linux the `Makefile` wraps the common commands (`make help`).

---

## Walkthrough

With the stack running (bash, `curl` and `python3`; on Windows use `python` instead). The
bookings are for fixed times, so running it a second time the same day gets
`409 DUPLICATE_BOOKING`; change the `days=` offsets to rerun it.

```bash
BASE=http://localhost:8000/api/v1
json() { python3 -c "import json,sys; print(json.load(sys.stdin)$1)"; }
pretty() { python3 -m json.tool; }
H='Content-Type: application/json'

# 1. Log in as the demo patient.
TOKEN=$(curl -s -X POST $BASE/auth/login/ -H "$H" \
  -d '{"email": "patient@eve.test", "password": "Patient-Demo-2026!"}' | json "['access']")
AUTH="Authorization: Bearer $TOKEN"

# 2. Where can I get an MRI of the brain, cheapest first?
MRI=$(curl -s "$BASE/tests/?search=MRI_BRAIN" | json "['results'][0]['id']")
curl -s "$BASE/tests/$MRI/centres/" | pretty
CENTRE=$(curl -s "$BASE/tests/$MRI/centres/" | json "['results'][0]['centre']['id']")

# 3. Is 10:00 India time free three days from now? (Slot 12: 07:00 opening + 12 × 15 minutes.)
DAY=$(python3 -c "import datetime as d; print(d.date.today() + d.timedelta(days=3))")
curl -s "$BASE/centres/$CENTRE/tests/$MRI/availability/?date=$DAY" | json "['slots'][12]"

# 4. Book it, with an Idempotency-Key so the request is safe to retry: sending it again returns
#    the same booking instead of a duplicate.
KEY=$(python3 -c "import uuid; print(uuid.uuid4())")
BOOK="{\"centre_id\": \"$CENTRE\", \"test_id\": \"$MRI\", \"appointment_at\": \"${DAY}T10:00:00+05:30\"}"
BOOKING=$(curl -s -X POST $BASE/bookings/ -H "$AUTH" -H "$H" -H "Idempotency-Key: $KEY" -d "$BOOK" | json "['id']")
curl -s -X POST $BASE/bookings/ -H "$AUTH" -H "$H" -H "Idempotency-Key: $KEY" -d "$BOOK" | json "['id']"  # same id

# 5. Pay. (The brief's literal path, POST /payments/, works too.)
curl -s -X POST $BASE/payments/ -H "$AUTH" -H "$H" \
  -d "{\"booking_id\": \"$BOOKING\", \"payment_method\": \"mock_success\"}" | pretty

# 6. The booking is now CONFIRMED, with its payment and its status history.
curl -s $BASE/bookings/$BOOKING/ -H "$AUTH" | pretty

# 7. A UPI-style payment: 202 now; the provider's webhook confirms it ~12 s later.
SLOT2=$(python3 -c "import datetime as d; print(f'{d.date.today() + d.timedelta(days=4)}T10:00:00+05:30')")
BOOKING2=$(curl -s -X POST $BASE/bookings/ -H "$AUTH" -H "$H" \
  -d "{\"centre_id\": \"$CENTRE\", \"test_id\": \"$MRI\", \"appointment_at\": \"$SLOT2\"}" \
  | json "['id']")
PAYMENT=$(curl -s -X POST $BASE/payments/ -H "$AUTH" -H "$H" \
  -d "{\"booking_id\": \"$BOOKING2\", \"payment_method\": \"mock_async_success\"}" | json "['id']")
sleep 15 && curl -s $BASE/bookings/$BOOKING2/ -H "$AUTH" | json "['status']"   # CONFIRMED

# 8. Deliver a signed provider event three times: the first is a no-op (already applied by the
#    provider's own webhook), the repeats are duplicates. Nothing changes.
docker compose exec api python manage.py send_webhook $PAYMENT --times 3
```

The booking after step 6 (some fields trimmed):

```json
{
  "id": "016b136e-a27c-4471-b9fc-72f6590c8c76",
  "status": "CONFIRMED",
  "status_reason": null,
  "centre": {"id": "f0e49699-…", "name": "Cyber City Imaging Centre", "city": "Gurugram", "…": "…"},
  "test": {"id": "a0485bf4-…", "code": "MRI_BRAIN", "name": "MRI Brain (Plain)", "category": "RADIOLOGY"},
  "appointment_at": "2026-10-02T04:30:00Z",
  "amount": 650000,
  "currency": "INR",
  "hold_expires_at": "2026-09-29T06:53:33.294619Z",
  "confirmed_at": "2026-09-29T06:38:33.670191Z",
  "payments": [
    {"id": "01886418-…", "status": "SUCCESS", "amount": 650000, "method": "mock_success", "…": "…"}
  ],
  "history": [
    {"from_status": null, "to_status": "PENDING", "actor_type": "USER", "payment_id": null, "…": "…"},
    {"from_status": "PENDING", "to_status": "CONFIRMED", "actor_type": "PROVIDER", "payment_id": "01886418-…", "…": "…"}
  ]
}
```

Paying for it again returns an error. Every error has this shape:

```json
{
  "type": "urn:eve-diagnostics:problem:booking-already-paid",
  "title": "Booking already paid",
  "status": 409,
  "detail": "This booking is already confirmed and paid for.",
  "instance": "/api/v1/payments/",
  "code": "BOOKING_ALREADY_PAID",
  "request_id": "0edb755a097848c9a22db5806517778f",
  "booking_id": "016b136e-a27c-4471-b9fc-72f6590c8c76"
}
```

Validation errors list each problem, with a machine-readable code per field:

```json
{
  "type": "urn:eve-diagnostics:problem:validation-error",
  "title": "Invalid request",
  "status": 400,
  "code": "VALIDATION_ERROR",
  "errors": [
    {"field": "appointment_at", "code": "not_on_slot_boundary",
     "message": "Appointments start every 15 minutes (e.g. 10:00, 10:15)."}
  ],
  "…": "…"
}
```

---

## API reference

The complete, interactive reference is Swagger UI at `/api/docs/` (OpenAPI 3.1 at
`/api/v1/schema/`), with request and response schemas for every endpoint.

**Conventions**

- Versioned under `/api/v1/`. The brief's literal paths `POST /payments/` and
  `POST /payments/webhook/` are served too, by the same views. Trailing slashes are optional and
  nothing is ever redirected, since a redirect turns a POST into a GET in most clients and counts
  as a failed delivery for webhook senders.
- Authentication: `Authorization: Bearer <access token>` from `POST /api/v1/auth/login/`. Access
  tokens last 15 minutes; refresh tokens last 7 days and are rotated (the old one is revoked).
- Money is an integer in paise plus a currency: `"amount": 650000, "currency": "INR"` is ₹6,500.00.
- Times are ISO 8601. Inputs must carry a UTC offset; responses are in UTC.
- Lists are paginated: `?page=` / `?page_size=` (max 100) for the catalogue, and a cursor
  (`next` / `previous` links) for bookings.
- Unknown request fields are rejected with `400` rather than silently ignored, so a typo or an
  attempt to set `amount`, `status` or `role` never passes unnoticed.
- Every response carries an `X-Request-ID` header (a well-formed incoming one is kept), which also
  appears in every log line and in error bodies.
- `POST /bookings/` and `POST /payments/` accept an optional `Idempotency-Key` header; see
  [retrying safely](#retrying-safely-idempotency-key).
- Public catalogue reads carry `X-Cache: HIT` or `MISS`; see [caching](#caching-the-catalogue).

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/api/v1/auth/signup/` | — | Create a patient account |
| POST | `/api/v1/auth/login/` | — | Email + password → access and refresh tokens |
| POST | `/api/v1/auth/token/refresh/` | refresh token | Rotate the token pair |
| POST | `/api/v1/auth/logout/` | refresh token | Revoke a refresh token |
| GET | `/api/v1/auth/me/` | bearer | The authenticated user |
| GET | `/api/v1/centres/` | — | Centres; filters `city`, `test` (id), `search`; `ordering` |
| POST | `/api/v1/centres/` | admin | Create a centre |
| GET | `/api/v1/centres/{id}/` | — | A centre with the tests it offers and their prices |
| PATCH / DELETE | `/api/v1/centres/{id}/` | admin | Update / deactivate a centre |
| GET | `/api/v1/centres/{id}/tests/` | — | Tests offered at a centre; `ordering=price` |
| POST | `/api/v1/centres/{id}/tests/` | admin | Offer a test: `{"test_id", "price", "slot_capacity"}` (capacity optional) |
| GET / PATCH / DELETE | `/api/v1/centres/{id}/tests/{test_id}/` | — / admin / admin | One offering; change its price or capacity; withdraw it |
| GET | `/api/v1/centres/{id}/tests/{test_id}/availability/?date=` | — | That day's slots, with places left and whether each is bookable now |
| GET | `/api/v1/tests/` | — | The test catalogue; filters `category`, `search` |
| POST | `/api/v1/tests/` | admin | Add a test (the code is stored upper-case) |
| GET / PATCH / DELETE | `/api/v1/tests/{id}/` | — / admin / admin | One test; update; deactivate |
| GET | `/api/v1/tests/{id}/centres/` | — | Price comparison: centres offering it, cheapest first; filter `city` |
| POST | `/api/v1/bookings/` | bearer | Book a test: `{"centre_id", "test_id", "appointment_at"}` |
| GET | `/api/v1/bookings/` | bearer | My bookings, newest first (admins: all); filters `status`, `appointment_from`, `appointment_to` |
| GET | `/api/v1/bookings/{id}/` | owner / admin | A booking with its payments and status history |
| POST | `/api/v1/bookings/{id}/cancel/` | owner / admin | Cancel (idempotent) |
| POST | `/api/v1/payments/` | owner | Pay for a booking: `{"booking_id", "payment_method"}` |
| GET | `/api/v1/payments/{id}/` | owner / admin | A payment's status |
| POST | `/api/v1/payments/webhook/` | provider signature | Payment-provider events |
| GET | `/api/v1/health/live/`, `/api/v1/health/ready/` | — | Liveness; readiness (database, cache) |

**Error codes**

| Status | Codes |
|---|---|
| 400 | `VALIDATION_ERROR` (with a list of `{field, code, message}`), `MALFORMED_REQUEST`, `WEBHOOK_PAYLOAD_INVALID` |
| 401 | `AUTHENTICATION_REQUIRED`, `TOKEN_INVALID`, `INVALID_CREDENTIALS`, `AUTHENTICATION_FAILED`, `WEBHOOK_SIGNATURE_INVALID` |
| 403 | `PERMISSION_DENIED` |
| 404 | `NOT_FOUND`, `CENTRE_NOT_FOUND`, `TEST_NOT_FOUND`, `OFFERING_NOT_FOUND`, `BOOKING_NOT_FOUND`, `PAYMENT_NOT_FOUND` |
| 405 / 415 | `METHOD_NOT_ALLOWED` / `UNSUPPORTED_MEDIA_TYPE` |
| 409 | `EMAIL_ALREADY_REGISTERED`, `CENTRE_ALREADY_EXISTS`, `TEST_CODE_ALREADY_EXISTS`, `OFFERING_ALREADY_EXISTS`, `TEST_INACTIVE`, `DUPLICATE_BOOKING`, `SLOT_FULL`, `INVALID_STATE_TRANSITION`, `CANCELLATION_WINDOW_CLOSED`, `BOOKING_ALREADY_PAID`, `BOOKING_NOT_PAYABLE`, `BOOKING_EXPIRED`, `PAYMENT_IN_PROGRESS` |
| 422 | `TEST_NOT_OFFERED`, `IDEMPOTENCY_KEY_REUSED` |
| 429 | `RATE_LIMITED` (with `Retry-After`) |
| 500 / 503 | `INTERNAL_ERROR` / `SERVICE_UNAVAILABLE` (with `Retry-After`; safe to retry) |

---

## How it works

### Architecture

```mermaid
flowchart LR
    client["Client / Swagger UI"] -->|"HTTPS + Bearer JWT"| api["API<br/>Django + DRF"]
    api --> pg[("PostgreSQL<br/>source of truth")]
    api --> redis[("Redis<br/>cache · rate limits · broker")]
    beat["Celery beat"] --> redis
    worker["Celery worker"] --> redis
    worker --> pg
    api -->|"charge()"| mockpay["MockPay<br/>simulated provider"]
    mockpay -.->|"queue webhook"| redis
    worker -->|"signed webhook,<br/>at least once"| api
```

A modular monolith with one app per domain: `accounts`, `catalog`, `bookings`, `payments`, and
`mockpay` (the simulated provider), plus `core` for shared plumbing. Inside each app:

- `apis.py` handles HTTP only: validate the input, call a service or selector, serialize the output.
- `services.py` holds the write workflows: transactions, locks, business rules.
- `selectors.py` holds the reads, always scoped to what the caller may see.
- `domain.py` holds pure rules (state machines, appointment policy, slot grid, payment
  decisions), with no database or clock, so they are tested exhaustively.

`payments` reaches the provider only through a `PaymentGateway` interface (charge, fetch status)
and signed webhooks. MockPay implements it; a Razorpay adapter would implement the same three
members without touching bookings or payments.

### Booking lifecycle

```mermaid
stateDiagram-v2
    [*] --> PENDING : book (price snapshotted, 15-minute hold)
    PENDING --> CONFIRMED : payment succeeded
    PENDING --> FAILED : payment declined, or hold expired
    PENDING --> CANCELLED : cancelled (no payment in flight)
    CONFIRMED --> CANCELLED : cancelled at least 2 h ahead (refund owed)
    FAILED --> [*]
    CANCELLED --> [*]
```

Every status change goes through one function that checks the transition table, stamps the
matching timestamp and appends to an audit trail (`booking_status_events`), all in the caller's
transaction. `FAILED` and `CANCELLED` are final: retrying after a declined payment means a new
booking, which re-checks the price and the appointment rules.

### Slot capacity

An offering can limit how many patients one 15-minute slot takes (`slot_capacity`): an MRI
scanner takes one, while sample collection has no limit (`null`). A slot's place is taken by a
`CONFIRMED` booking, or by a `PENDING` one that is still holding it: before its hold lapses, or
while a payment is in flight, because that payment may yet confirm it. A lapsed hold frees the
place at once, without waiting for the sweeper.

Places are counted, not kept in a counter, so cancelling, failing and expiring have nothing to
update. The count and the insert happen under a PostgreSQL advisory lock on that one slot
(`pg_advisory_xact_lock`, released at commit). Two patients can't both take its last place, and
bookings for other slots never wait. A full slot is `409 SLOT_FULL`; if one of the places is the
caller's own, the answer is `409 DUPLICATE_BOOKING`, which says more.

`GET /centres/{id}/tests/{test_id}/availability/?date=2026-10-05` lists every slot of that day
(in the centre's time zone) with the places left and whether a booking for it would be accepted
right now. It uses the same rules as booking, and one grouped query counts the places.

```json
{"date": "2026-10-05", "timezone": "Asia/Kolkata", "slot_minutes": 15, "slot_capacity": 1,
 "slots": [{"start": "2026-10-05T04:30:00Z", "remaining": 0, "available": false}, "…"]}
```

### Payments

`POST /payments/` runs in three steps:

1. Lock the booking, check it can be paid, and record the payment as `PENDING`; commit.
2. Ask the provider to charge, **holding no locks** (a slow provider must not block anyone), using
   our payment id as the provider's idempotency key.
3. Apply the provider's answer.

A crash after step 1 or 2 leaves a `PENDING` payment that the webhook or reconciliation completes,
because the provider knows the charge by our id. If the provider can't be reached the outcome is
*unknown*, not failed, so the payment stays `PENDING` (`202`).

Every result, whichever way it arrives, is applied by `payment_apply_result`. It locks the
booking and then the payment (the one lock order in the codebase, so no deadlocks) and asks a pure
function what the result means:

| Payment | Result reported | Booking | Outcome |
|---|---|---|---|
| PENDING | SUCCESS | PENDING | Payment `SUCCESS`, booking `CONFIRMED` |
| PENDING | FAILED | PENDING | Payment `FAILED`, booking `FAILED` |
| any | the same as now, or "still processing" | any | Nothing changes (`noop`) |
| SUCCESS | FAILED | any | Ignored as stale: captured money is final |
| PENDING or FAILED | SUCCESS | FAILED or CANCELLED | Payment `SUCCESS` with a refund owed; the booking stays closed |

The last row covers late authorisation. Banks sometimes approve a payment after it was reported as
failed (Razorpay documents this). The money is real, so it's recorded and flagged for refund
instead of resurrecting a booking that already failed or was cancelled.

Test payment methods choose MockPay's behaviour, like a provider's test cards:

| `payment_method` | Response | Result |
|---|---|---|
| `mock_success` | `201` | `SUCCESS`, booking `CONFIRMED` |
| `mock_decline`, `mock_insufficient_funds` | `201` | `FAILED` with a `failure_code`; booking `FAILED` |
| `mock_async_success`, `mock_async_failure` | `202` | `PENDING`; the result arrives by webhook ~12 s later (UPI-collect style) |
| `mock_gateway_error` | `202` | The provider charged but the response was lost; the webhook reports it |
| `mock_random` (default) | `201` | Succeeds 80% of the time |

Only the booking's patient can pay, and the amount is always the booking's snapshot. A booking can
have only one payment in flight or succeeded (enforced by the database), so a double click can't
charge twice. An unpaid booking whose hold expired is refused (`409 BOOKING_EXPIRED`), and
cancelling is refused while a payment is in flight (`409 PAYMENT_IN_PROGRESS`).

### Retrying safely: `Idempotency-Key`

The database already makes retries *safe*: a repeated booking is a `409 DUPLICATE_BOOKING` and a
repeated payment is a `409 PAYMENT_IN_PROGRESS`. A client that timed out, though, can't tell
whether its first attempt worked. With an `Idempotency-Key` header (as in the IETF
[draft](https://datatracker.ietf.org/doc/draft-ietf-httpapi-idempotency-key-header/); any 1–255
visible ASCII characters, such as a UUID, bare or as a quoted string), retries are also
*transparent*:

| Request | Response |
|---|---|
| A key the patient hasn't used | Processed as usual; the key is stored on the booking or payment it creates |
| The same key and the same request again | The resource the first request created, **as it is now**, with the original status code. Nothing is created or charged again |
| The same key with a different request | `422 IDEMPOTENCY_KEY_REUSED` |
| The same key, concurrently | Waits for the first request to commit, then gets its result |
| A request that was refused (4xx) | Nothing is stored, so the key can be used again |

- **The key lives on the row it created,** under a partial unique index, so the key and the
  resource are written in one `INSERT` and can't disagree. There is no separate response cache
  to keep consistent.
- **Keys are per patient and per endpoint.** Another patient's identical key is simply theirs.
- **Concurrent retries don't race.** A transaction-scoped advisory lock on the key is taken
  before anything else, so the second request looks the key up only after the first commits.
- **"The same request" means the same meaning.** It's compared by a hash of the validated
  values, so a retry that writes `10:00+05:30` as `04:30Z`, or omits the default payment method,
  still matches.
- **A replay comes first,** before any business rule: a retry gets its booking even if, by now,
  the time is too close to book.
- **A retry finishes an unfinished payment.** If the first attempt died after recording the
  payment but before charging, the replay runs the charge step again. That's safe because the
  provider's charge is itself idempotent on our payment id.

### The webhook

`POST /api/v1/payments/webhook/` accepts provider events signed per Standard Webhooks:
`webhook-id`, `webhook-timestamp` and `webhook-signature` (`v1,` + base64 HMAC-SHA256 over
`{id}.{timestamp}.{raw body}`). Signatures older than 5 minutes are refused, so a captured request
can't be replayed, and several secrets can be accepted at once, so a secret can be rotated without
downtime.

```json
{"type": "payment.succeeded", "timestamp": "2026-10-02T04:31:00Z",
 "data": {"payment_id": "…", "provider_payment_id": "mockpay_pay_…", "amount": 650000, "currency": "INR"}}
```

The event id is claimed (a unique `(provider, event_id)` row) **in the same transaction** that
applies the event. An event therefore can't be recorded without its effect, or applied twice. A
concurrent duplicate waits on the unique index, then sees the claim.

| Request | Response | Effect |
|---|---|---|
| Unsigned, badly signed or older than 5 minutes | `401` | Nothing stored: it may be forged or replayed |
| Signed but malformed | `400` | Nothing stored |
| A new event | `200 processed` | `applied`, or `noop` if the result was already known |
| The same `webhook-id` again, even concurrently | `200 duplicate` | Nothing changes |
| `payment.failed` after the payment succeeded | `200 ignored` | Stale; the captured payment stands |
| Success for a booking that already failed or was cancelled | `200 processed`, `refund_owed` | Payment recorded, refund flagged, booking unchanged |
| Unknown payment, or an amount/currency that doesn't match | `200 rejected` | Stored for review (the admin site lists them); nothing applied |
| A transient database failure | `503` | Nothing stored; retried in-process first, then by the provider |

Authentic events always get a `200`, even ones we can't apply: retrying can't fix them, and payment
providers disable endpoints that keep failing. To send a correctly signed event by hand:

```bash
docker compose exec api python manage.py send_webhook <payment_id>            # the charge's result
docker compose exec api python manage.py send_webhook <payment_id> --times 3  # delivered 3 times
docker compose exec api python manage.py send_webhook <payment_id> --status failed --amount 1
docker compose exec api python manage.py send_webhook <payment_id> --bad-signature
```

### Background jobs

No request depends on the Celery jobs for correctness: an expired hold is refused at payment time
anyway, and an unknown outcome stays `PENDING` rather than being guessed. The jobs keep state tidy
and make the provider behave like a real one.

| Job | When | What it does |
|---|---|---|
| MockPay webhook delivery | After each result | At least once: retries 5xx, 408, 429 and network errors with exponential backoff and jitter (up to 8 attempts), signs each attempt afresh, and delivers ~20% of events twice on purpose |
| MockPay settlement | ~10 s after an async charge | Settles the charge, then announces it by webhook |
| Hold sweeper | Every 60 s | Fails unpaid bookings whose hold lapsed, with `SELECT … FOR UPDATE SKIP LOCKED`; bookings with a payment in flight are left alone |
| Reconciliation | Every 60 s | Asks the provider about payments `PENDING` for over 2 minutes and applies the answer; "no such charge" counts as `FAILED` |

Tasks are acknowledged only after they finish (`acks_late`), so a crashed worker's task runs
again. That's safe because every task is idempotent. One `request_id` traces an asynchronous
payment through the API request, the settlement task and the webhook delivery.

### Caching the catalogue

The public catalogue GETs (centres, tests, offerings, price comparison) are served from Redis for
5 minutes. The catalogue is read far more than it changes, and every patient sees the same thing.
Administrators bypass the cache: they also see inactive records, and must see their own edits at
once. Availability is never cached, since it changes with every booking.

- **Invalidation is by version, not by key.** Responses are stored under the catalogue's current
  version (`catalog:{version}:{hash of the URL}`), and any write to a catalogue table replaces
  the version, making every older entry unreachable. The TTL only bounds staleness if a version
  change is ever lost.
- **Only after the write commits.** A reader running during the write can't cache the old data
  under the new version.
- **Every write counts.** Saves and deletes of centres, tests and offerings send a signal, so
  edits made in the admin site, which bypass the services, invalidate too.
- **Fails open.** Without Redis, reads go to the database. `X-Cache: HIT` or `MISS` shows which
  way a response went.

### Concurrency

| Race | What stops it |
|---|---|
| The same webhook delivered twice at once | Unique `(provider, event_id)`: the second insert waits, then sees the first |
| The immediate response and the webhook reporting the same result | Both lock booking → payment; the second is a `noop` |
| Two "Pay" clicks | The booking row lock, backed by the partial unique index "one live payment per booking" |
| Two identical "Book" requests | Partial unique index on `(user, offering, appointment_at)` for active bookings |
| Several patients booking a slot's last place | An advisory lock on that slot around the count and the insert |
| Retries with the same `Idempotency-Key` | An advisory lock on the key; the partial unique index on the key backs it up |
| Cancel racing a payment | The booking row lock orders them; cancel is refused while a payment is in flight |
| The sweeper racing a payment | Row locks with `SKIP LOCKED`; bookings with a payment in flight are skipped |

Transactions use PostgreSQL's default isolation (READ COMMITTED) with explicit locks, always
taken in one order: idempotency key, then slot or booking, then payment. Lock and statement
timeouts (3 s / 10 s) turn a stuck lock into a retryable `503` instead of a hung request.

---

## Database design

```mermaid
erDiagram
    users ||--o{ bookings : "books"
    diagnostic_centres ||--o{ offerings : "offers"
    diagnostic_tests ||--o{ offerings : "is offered as"
    offerings ||--o{ bookings : "is booked as"
    bookings ||--o{ booking_status_events : "history"
    bookings ||--o{ payments : "is paid by"
    payments ||--o{ webhook_events : "is reported by"

    users {
        uuid id PK
        varchar email UK "stored lower-case"
        varchar password "argon2id hash"
        varchar full_name
        varchar role "PATIENT or ADMIN"
        boolean is_active
    }
    diagnostic_centres {
        uuid id PK
        varchar name "unique per city, ignoring case"
        varchar address_line
        varchar city
        varchar state
        varchar pincode "6 digits"
        varchar timezone "IANA, e.g. Asia/Kolkata"
        time opens_at "before closes_at"
        time closes_at
        boolean is_active
    }
    diagnostic_tests {
        uuid id PK
        varchar code UK "e.g. MRI_BRAIN"
        varchar name
        varchar category "PATHOLOGY, RADIOLOGY, ..."
        text preparation
        boolean is_active
    }
    offerings {
        uuid id PK
        uuid centre_id FK
        uuid test_id FK
        integer price "paise, > 0"
        varchar currency
        integer slot_capacity "patients per slot; null = no limit"
        boolean is_active
    }
    bookings {
        uuid id PK
        uuid user_id FK
        uuid offering_id FK
        timestamptz appointment_at
        integer amount "price snapshot, paise"
        varchar status "PENDING CONFIRMED FAILED CANCELLED"
        varchar status_reason
        timestamptz hold_expires_at
        varchar idempotency_key "unique per user, if sent"
        varchar request_fingerprint
    }
    booking_status_events {
        bigint id PK
        uuid booking_id FK
        varchar from_status
        varchar to_status
        varchar actor_type "USER ADMIN SYSTEM PROVIDER"
        uuid payment_id FK "what caused it"
    }
    payments {
        uuid id PK "our reference at the provider"
        uuid booking_id FK
        integer amount
        varchar status "PENDING SUCCESS FAILED"
        varchar provider_payment_id
        varchar refund_status "empty, PENDING or PROCESSED"
        varchar idempotency_key "if sent"
        varchar request_fingerprint
    }
    webhook_events {
        bigint id PK
        varchar event_id "unique per provider"
        jsonb payload
        varchar status "PROCESSED IGNORED REJECTED"
        varchar outcome
    }
```

(`mockpay_charges` is the simulated provider's own ledger, deliberately separate from these.)

**Modelling choices**

- **Tests are a global catalogue; a centre *offers* a test at its own price.** The price lives on
  the `offerings` association, not on the test, because the same MRI costs different amounts at
  different centres.
- **A booking snapshots its amount.** Later price changes never touch it, and the payment always
  charges the snapshot, never a client-supplied figure.
- **Money is an integer number of paise**, as with Razorpay and Stripe. Floats can't represent
  money exactly.
- **UUID primary keys**, because ids appear in URLs and must not be guessable.
- **Catalogue rows are deactivated, never deleted.** Foreign keys `PROTECT` everything a booking
  or payment refers to, so history stays intact.
- **Statuses are `varchar` + `CHECK`**, not Postgres enums, so adding a state is a one-line
  migration. Django `choices` alone aren't enforced by the database, hence the explicit checks.

**What the database guarantees** (enforced even if application code has a bug or a race):

| Constraint | Guarantees |
|---|---|
| `bookings_one_active_per_slot`: unique `(user, offering, appointment_at)` where status is PENDING or CONFIRMED | No double booking from a double click; re-booking after a cancellation is allowed |
| `payments_one_live_per_booking`: unique `(booking)` where status is PENDING or SUCCESS | No double charge; failed attempts don't count |
| `webhook_events_uniq`: unique `(provider, event_id)` | Each provider event is applied at most once |
| `bookings_idempotency_key_uniq`, `payments_idempotency_key_uniq`: unique keys where one was sent | A retried request can't create a second booking or payment |
| `offerings_slot_capacity_positive`: null or > 0 | A slot takes at least one patient, or has no limit |
| `offerings_centre_test_uniq`: unique `(centre, test)` | One price per test per centre |
| `diagnostic_centres_name_city_uniq`: unique `(lower(name), lower(city))` | No duplicate centres differing only in letter case |
| `users_email_uniq` + `users_email_lowercase` | Case-insensitive unique emails, even for writes that bypass the ORM |
| `*_amount_positive`, `offerings_price_positive` | No zero or negative money |
| `bookings_confirmed_has_timestamp`, `payments_completed_has_timestamp` | A status and its timestamp can't disagree |
| `payments_refund_only_when_paid` | A refund can only be owed on money actually taken |
| `*_status_valid`, `*_format` checks | Only known statuses, and well-formed codes and PIN codes |

**Indexes** serve specific queries: "my bookings, newest first" (`user, created_at DESC`), "who
holds this slot?" (`offering, appointment_at`), "which unpaid holds lapsed?" (partial, `PENDING`
only), "payments stuck in `PENDING`" (partial), "where is this test offered, cheapest first?"
(`test, price` for active offerings) and "active centres in a city" (`lower(city)`, partial).
Foreign keys already covered by a composite index don't get a redundant single-column one.

---

## Edge cases

Each of these has a test. Selected cases:

| Scenario | Behaviour |
|---|---|
| Signup with an email already registered, in any letter case | `409 EMAIL_ALREADY_REGISTERED` |
| Signup sending `role: "ADMIN"` or other unknown fields | `400`: unknown field (roles are never writable) |
| Wrong password, unknown email or deactivated account | The same `401 INVALID_CREDENTIALS`, with similar timing |
| Expired, tampered, unsigned (`alg: none`), wrong-audience, or refresh-used-as-access token | `401 TOKEN_INVALID` |
| A patient trying to create or change a centre, test or price | `403 PERMISSION_DENIED` (`401` if not logged in) |
| Another patient's booking or payment: read, cancel or pay | `404`, exactly like one that doesn't exist |
| Malformed id in a URL / body | `404` / `400` |
| Booking a test the centre doesn't offer, or has withdrawn | `422 TEST_NOT_OFFERED` |
| Appointment in the past, too soon, too far ahead, off a 15-minute slot, outside opening hours, or without a UTC offset | `400` with the specific reason (`in_past`, `too_soon`, …) |
| The same booking submitted twice (even concurrently) | One booking; `409 DUPLICATE_BOOKING` naming it |
| The same booking or payment retried with its `Idempotency-Key` (even concurrently) | The original resource, as it is now; nothing created or charged twice |
| An `Idempotency-Key` reused for a different request / malformed | `422 IDEMPOTENCY_KEY_REUSED` / `400` |
| A slot at capacity, including many patients racing for its last place | `409 SLOT_FULL`; never overbooked |
| An unpaid booking's hold lapsed, but its payment is still in flight | It keeps its place: the payment may still confirm it |
| A price change after booking | The booking and its payment keep the booked price |
| Paying a booking that is already paid, failed, cancelled or expired | `409 BOOKING_ALREADY_PAID` / `BOOKING_NOT_PAYABLE` / `BOOKING_EXPIRED` |
| Two simultaneous payments for one booking | One charge; the other gets `409` |
| A declined payment | `201` with `status: FAILED` and a `failure_code`; booking `FAILED` |
| Provider unreachable during payment | `202 PENDING`; the webhook or reconciliation settles it |
| Cancelling while a payment is in flight / within 2 h of a paid appointment | `409 PAYMENT_IN_PROGRESS` / `409 CANCELLATION_WINDOW_CLOSED` |
| Cancelling twice | `200`, no change |
| Webhook: repeated, concurrent, out of order, late success, unknown payment, wrong amount, forged, replayed, malformed | See [the webhook](#the-webhook) |
| A crash while processing a webhook | Nothing is persisted; the redelivered event succeeds |
| A catalogue change, including one made in the admin site | Cached reads are retired as soon as it commits |
| Redis down | Rate limiting and caching fail open; payments still complete; lost task enqueues are logged and reconciliation catches up |
| A lock held by a concurrent request for over 3 s | `503 SERVICE_UNAVAILABLE` with `Retry-After` |
| Too many login attempts | `429 RATE_LIMITED` with `Retry-After` |

---

## Tests and quality checks

```bash
docker compose up -d db redis        # tests run against real PostgreSQL, never SQLite
uv run pytest --cov                  # 462 tests, ~98% coverage
uv run ruff check . && uv run ruff format --check .
uv run python manage.py makemigrations --check --dry-run
```

- **Pure rules, exhaustively:** the booking transition table (all 16 pairs), the payment
  decision function over all 36 inputs (checked against its stated rules), the appointment
  policy at every boundary, and the slot grid checked minute by minute against the booking rules.
- **Database guarantees:** each constraint is tested by writing around the application, as a raw
  update or a bulk insert would.
- **Every endpoint:** success paths, the edge cases above, permissions, and the problem+json shape.
  Query-count tests make sure list endpoints don't turn into N+1 queries as data grows.
- **Concurrency, on real threads and connections:** 8 identical bookings, payments, keyed retries
  or webhook deliveries released at the same instant each take effect exactly once; 8 patients
  racing for a 2-place slot get exactly 2 places; `SKIP LOCKED` is verified. Each of these tests
  was checked to fail with its lock removed.
- **Crash safety:** a failure injected between recording a webhook event and applying it leaves no
  trace, and the retry succeeds.
- **End to end:** an asynchronous payment settles through the task chain and a signed webhook
  delivered twice over real HTTP to a live server, confirming the booking once.
- The webhook signature code reproduces the Standard Webhooks specification's published example.

Tests run on PostgreSQL because the design relies on Postgres behaviour: partial unique indexes,
check constraints, row and advisory locks, and `SKIP LOCKED`. GitHub Actions runs lint, format, the migration
check and the full suite against PostgreSQL 18, and builds the production image.

---

## Configuration

Everything is configured through environment variables (`.env` locally; see `.env.example`).

| Variable | Default | Purpose |
|---|---|---|
| `DJANGO_SECRET_KEY`, `JWT_SIGNING_KEY` | required | Django and JWT signing secrets |
| `WEBHOOK_SECRETS` | required | Comma-separated `whsec_…` secrets accepted on the webhook (several allow rotation) |
| `DATABASE_URL` | `postgres://eve:eve@localhost:5433/eve` | PostgreSQL |
| `REDIS_URL` / `CELERY_BROKER_URL` | `redis://localhost:6380/0` / `…/1` | Cache and rate limits / task broker |
| `CATALOG_CACHE_SECONDS` | `300` | How long public catalogue reads are cached (`0` turns the cache off) |
| `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS` | `false`, `localhost,127.0.0.1` | |
| `LOG_JSON`, `LOG_LEVEL` | JSON unless debugging, `INFO` | Structured logs |
| `NUM_PROXIES` | `0` | Trusted proxies in front of the app (for rate-limit client IPs) |
| `BOOKING_HOLD_MINUTES` | `15` | How long an unpaid booking holds its slot |
| `BOOKING_MIN_LEAD_MINUTES`, `BOOKING_MAX_ADVANCE_DAYS`, `BOOKING_SLOT_MINUTES` | `60`, `30`, `15` | Appointment rules |
| `BOOKING_CANCELLATION_CUTOFF_HOURS` | `2` | Confirmed bookings can be cancelled until this long before |
| `PAYMENT_GATEWAY` | `apps.mockpay.gateway.MockPayGateway` | The provider adapter |
| `PAYMENT_RECONCILE_AFTER_SECONDS` | `120` | When a `PENDING` payment is checked with the provider |
| `WEBHOOK_TOLERANCE_SECONDS` | `300` | Replay window for webhook signatures |
| `MOCKPAY_WEBHOOKS_ENABLED`, `MOCKPAY_WEBHOOK_URL` | `true`, local API | Whether and where MockPay pushes webhooks |
| `MOCKPAY_SUCCESS_RATE`, `MOCKPAY_DUPLICATE_DELIVERY_RATE` | `0.8`, `0.2` | Simulated behaviour |
| `THROTTLE_AUTH_LOGIN`, `THROTTLE_AUTH_SIGNUP`, `THROTTLE_PAYMENTS`, `THROTTLE_USER`, `THROTTLE_ANON` | `10/min`, `20/hour`, `30/min`, `600/min`, `120/min` | Rate limits |

---

## Assumptions

- **The patient is the logged-in user.** Booking for family members is out of scope.
- **Administrators are EVE operations staff.** They manage the catalogue and can see and cancel
  any booking. Public signup always creates a patient; administrators are created with
  `createsuperuser` (or the demo seed).
- **Prices are per centre.** The same test can cost different amounts at different centres.
- **INR only.** Amounts are integers in paise; the `currency` column keeps the door open.
- **Appointments are wall-clock times at the centre:** 15-minute slots within daily opening hours,
  at least an hour and at most 30 days ahead.
- **Capacity is per offering and per slot.** An MRI scanner at a centre takes one patient per
  slot. Tests on different machines don't compete for places, and a centre's overall staff
  capacity isn't modelled.
- **A declined payment fails the booking,** as the brief suggests. Trying again means a new
  booking, which re-checks the price and the time.
- **An unpaid booking holds its slot for 15 minutes**, then fails with `PAYMENT_TIMEOUT`.
- **Cancelling a paid booking records a refund as owed** (`refund_status: PENDING`). Executing
  refunds is out of scope.
- **The mock provider decides outcomes from test payment methods.** In a real deployment the
  method would be a tokenised card or UPI handle, and the adapter would call the provider's API.
- **The provider identifies payments by our payment id**, sent as its idempotency key. Its webhook
  events carry that id, the provider's own payment id, and the amount, which must match.

---

## What I'd improve with more time

- **A real payment gateway:** a Razorpay adapter behind the same interface (orders, checkout
  signature verification, webhooks, a status API for reconciliation), plus a refund workflow driven
  by `refund_status`.
- **Richer scheduling:** weekly timetables and holidays instead of fixed daily hours, machines
  shared by several tests (MRI brain and MRI spine on one scanner), and home sample collection
  by PIN code.
- **Idempotency-key expiry:** keys are kept for as long as their booking or payment exists. A
  24-hour window, as Stripe uses, would let clients reuse keys, at the cost of a cleanup job.
- **Notifications** (booking confirmed, refund owed) through a transactional outbox.
- **A partner-centre role** that manages only its own centre's prices and schedule.
- **Operations:** metrics and tracing (OpenTelemetry), alerts on rejected webhooks and refunds
  owed, and an admin action to replay rejected events once the cause is fixed.
- **Patient domain:** family profiles, prescriptions for CT and MRI referrals, report delivery,
  and phone OTP login.
- **Scale:** UUIDv7 keys for index locality, partitioning bookings and payments by month,
  PgBouncer, and load tests of the booking and payment paths.

---

## Project layout

```
config/            settings (environment-driven), URLs, Celery app
apps/
  core/            problem+json errors, request ids & JSON logging, pagination, permissions,
                   Standard Webhooks signing, Idempotency-Key handling, DB helpers (advisory
                   locks, transient-error retries), fail-open Redis cache, health checks
  accounts/        user model (email login, roles), signup / login / refresh / logout / me
  catalog/         centres, tests, offerings (price, slot capacity); filters; read cache;
                   demo seed command
  bookings/        booking model + audit trail, state machine, appointment rules & slot grid
                   (domain.py), capacity & availability, services, hold sweeper task
  payments/        payments, webhook inbox, gateway interface, decide() (domain.py),
                   services, webhook handling, reconciliation task
  mockpay/         the simulated provider: ledger, gateway adapter, webhook delivery tasks,
                   send_webhook command
  */tests/         tests next to the code they cover
conftest.py        shared fixtures
Dockerfile         multi-stage, non-root, with a health check
docker-compose.yml api, worker, beat, db, redis, migrate
.github/workflows  CI: lint, format, migration check, tests on PostgreSQL, image build
```
