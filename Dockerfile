# syntax=docker/dockerfile:1

# ---- build: resolve the locked dependencies into a virtualenv ----------------------------------
FROM python:3.13-slim AS build

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project


# ---- runtime: the virtualenv, the source, an unprivileged user ---------------------------------
FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=config.settings

RUN useradd --create-home --uid 10001 app
WORKDIR /app

COPY --from=build /opt/venv /opt/venv
COPY --chown=app:app . .

# Static files for the admin and Swagger UI. The throwaway secrets exist only for this command.
RUN DJANGO_SECRET_KEY=build-only JWT_SIGNING_KEY=build-only WEBHOOK_SECRETS=whsec_YnVpbGQ= \
    python manage.py collectstatic --noinput --verbosity 0

USER app
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/live/', timeout=2)"]

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "30", "--graceful-timeout", "20"]
