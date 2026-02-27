"""
Cliente HTTP compartido para todos los servicios de agent_ventas.

Inicialización lazy: el cliente se crea en la primera llamada a get_client()
y se cierra limpiamente en el lifespan del servidor (close_http_client).
Esto permite reutilizar el connection pool entre todas las llamadas a las APIs
de MaravIA (ws_informacion_ia, ws_preguntas_frecuentes).

post_with_retry: wrapper con retry automático (tenacity) para operaciones de
LECTURA. No usar en operaciones de escritura por riesgo de duplicados si el
servidor recibió la request pero la respuesta timeouteó.
"""

import json
import logging
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

try:
    from .. import config as app_config
    from ..logger import get_logger
except ImportError:
    from ventas import config as app_config
    from ventas.logger import get_logger

logger = get_logger(__name__)

_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    """Devuelve el cliente HTTP compartido; lo crea en la primera llamada (lazy init)."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=5.0,
                read=app_config.API_TIMEOUT,
                write=5.0,
                pool=2.0,
            ),
            limits=httpx.Limits(
                max_connections=50,
                max_keepalive_connections=20,
                keepalive_expiry=30.0,
            ),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
    return _client


async def close_http_client() -> None:
    """Cierra el cliente HTTP compartido. Llamar en el teardown del servidor (lifespan)."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


@retry(
    stop=stop_after_attempt(app_config.HTTP_RETRY_ATTEMPTS),
    wait=wait_exponential(min=app_config.HTTP_RETRY_WAIT_MIN, max=app_config.HTTP_RETRY_WAIT_MAX),
    retry=retry_if_exception_type(httpx.TransportError),
    reraise=True,
)
async def post_with_retry(url: str, json: dict[str, Any]) -> dict[str, Any]:
    """
    POST con retry automático para errores de red transitoria.

    Intentos y tiempos de espera configurables vía:
      HTTP_RETRY_ATTEMPTS  (default 3)
      HTTP_RETRY_WAIT_MIN  (default 1s)
      HTTP_RETRY_WAIT_MAX  (default 4s)

    Reintenta solo httpx.TransportError (timeouts, connect errors).
    NO reintenta httpx.HTTPStatusError (respuestas 4xx/5xx del servidor).

    ADVERTENCIA: usar solo en operaciones de LECTURA idempotentes.
    """
    client = get_client()
    response = await client.post(url, json=json)
    response.raise_for_status()
    return response.json()


async def post_informacion(payload: dict[str, Any]) -> dict[str, Any]:
    """
    POST a ws_informacion_ia.php con logging DEBUG y retry automático (tenacity).

    Raises:
        httpx.HTTPStatusError: Si status code no 2xx
        httpx.TransportError: Error de red/timeout (después de agotar reintentos)

    Returns:
        Dict parseado del JSON de respuesta
    """
    cod_ope = payload.get("codOpe", "")

    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(
            "[API_INFORMACION] POST %s - %s",
            app_config.API_INFORMACION_URL,
            json.dumps(payload, ensure_ascii=False),
        )

    try:
        data = await post_with_retry(app_config.API_INFORMACION_URL, payload)

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "[API_INFORMACION] Response (codOpe=%s): %s",
                cod_ope,
                json.dumps(data, ensure_ascii=False),
            )

        return data

    except (httpx.HTTPStatusError, httpx.TransportError) as e:
        logger.warning(
            "[API_INFORMACION] %s (codOpe=%s): %s",
            type(e).__name__,
            cod_ope,
            e,
        )
        raise
    except Exception as e:
        logger.error(
            "[API_INFORMACION] Error inesperado (codOpe=%s): %s: %s",
            cod_ope, type(e).__name__, e,
            exc_info=True,
        )
        raise


__all__ = ["get_client", "close_http_client", "post_with_retry", "post_informacion"]
