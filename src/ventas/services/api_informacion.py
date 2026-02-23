"""
Cliente HTTP para ws_informacion_ia.php.
Helper compartido para categorías, sucursales y búsqueda de productos.
"""

import json
import logging
from typing import Any

import httpx

try:
    from .. import config as app_config
except ImportError:
    from ventas import config as app_config

logger = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    """Devuelve el cliente HTTP compartido; lo crea en la primera llamada (lazy init)."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=app_config.API_TIMEOUT)
    return _client


async def close_http_client() -> None:
    """Cierra el cliente HTTP compartido. Llamar en el teardown del servidor (lifespan)."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def post_informacion(payload: dict[str, Any]) -> dict[str, Any]:
    """
    POST a ws_informacion_ia.php.

    Raises:
        httpx.HTTPStatusError: Si status code no 2xx
        httpx.RequestError: Error de conexión
        httpx.TimeoutException: Timeout

    Returns:
        Dict parseado del JSON de respuesta
    """
    logger.debug(
        "[API_INFORMACION] POST %s - %s",
        app_config.API_INFORMACION_URL,
        json.dumps(payload, ensure_ascii=False),
    )
    cod_ope = payload.get("codOpe", "")
    try:
        client = get_client()
        response = await client.post(
            app_config.API_INFORMACION_URL,
            json=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPStatusError, httpx.RequestError, httpx.TimeoutException) as e:
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


__all__ = ["post_informacion", "close_http_client", "get_client"]
