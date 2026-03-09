# syntax=docker/dockerfile:1
ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV TZ=America/Lima

WORKDIR /app

# Usuario no privilegiado (buenas prácticas)
ARG UID=10001
RUN adduser \
    --disabled-password \
    --gecos "" \
    --home "/nonexistent" \
    --shell "/sbin/nologin" \
    --no-create-home \
    --uid "${UID}" \
    appuser

# Instalar uv (gestor de paquetes rápido)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Instalar paquete (pyproject.toml + src/)
COPY pyproject.toml .
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system .

USER appuser

EXPOSE 8001

CMD ["python", "-m", "ventas.main"]
