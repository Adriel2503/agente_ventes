"""
Contexto de negocio: fetch desde API MaravIA para el system prompt.
Usa OBTENER_CONTEXTO_NEGOCIO (ws_informacion_ia.php).
Mismo patrón que agent_citas: cache TTL + circuit breaker + retry con backoff.
"""

import asyncio
import logging
from typing import Any

from cachetools import TTLCache

try:
    from .api_informacion import post_informacion
except ImportError:
    from ventas.services.api_informacion import post_informacion

logger = logging.getLogger(__name__)

# Cache TTL: mismo criterio que citas (max 500 empresas, 1 hora)
_contexto_cache: TTLCache = TTLCache(maxsize=500, ttl=3600)  # id_empresa -> contexto (str)

# Circuit breaker: TTL 5 min para auto-reset de fallos
_contexto_failures: TTLCache = TTLCache(maxsize=500, ttl=300)  # id_empresa -> failure_count (int)
_contexto_failure_threshold = 3


def _is_contexto_circuit_open(id_empresa: Any) -> bool:
    """True si el circuit breaker está abierto para esta empresa."""
    failure_count = _contexto_failures.get(id_empresa, 0)
    return failure_count >= _contexto_failure_threshold


async def fetch_contexto_negocio(id_empresa: Any | None) -> str | None:
    """
    Obtiene el contexto de negocio desde la API para inyectar en el system prompt.
    Incluye cache TTL (1 h), circuit breaker (3 fallos -> abierto 5 min) y retry con backoff.

    Args:
        id_empresa: ID de la empresa (int o str). Si es None, retorna None.

    Returns:
        String con el contexto de negocio o None si no hay o falla.
    """
    if id_empresa is None or id_empresa == "":
        return None

    # 1. Cache
    if id_empresa in _contexto_cache:
        contexto = _contexto_cache[id_empresa]
        logger.debug(
            "[CONTEXTO_NEGOCIO] Cache HIT id_empresa=%s (%s caracteres)",
            id_empresa, len(contexto) if contexto else 0
        )
        return contexto if contexto else None

    # 2. Circuit breaker
    if _is_contexto_circuit_open(id_empresa):
        logger.warning("[CONTEXTO_NEGOCIO] Circuit abierto para id_empresa=%s", id_empresa)
        return None

    # 3. Retry con backoff (2 intentos, 1s y 2s)
    max_retries = 2
    payload = {
        "codOpe": "OBTENER_CONTEXTO_NEGOCIO",
        "id_empresa": id_empresa,
    }

    failed_by_exception = False
    for attempt in range(max_retries):
        try:
            logger.debug(
                "[CONTEXTO_NEGOCIO] Obteniendo contexto id_empresa=%s intento %d/%d",
                id_empresa, attempt + 1, max_retries
            )
            data = await post_informacion(payload)
            if not data.get("success"):
                logger.warning(
                    "[CONTEXTO_NEGOCIO] API sin éxito id_empresa=%s: %s",
                    id_empresa, data.get("error")
                )
                return None
            contexto = data.get("contexto_negocio") or ""
            contexto = str(contexto).strip() if contexto else ""
            if contexto:
                logger.info(
                    "[CONTEXTO_NEGOCIO] Respuesta recibida id_empresa=%s, longitud=%s caracteres",
                    id_empresa, len(contexto)
                )
            else:
                logger.info("[CONTEXTO_NEGOCIO] Respuesta recibida id_empresa=%s, contexto vacío", id_empresa)
            _contexto_cache[id_empresa] = contexto
            _contexto_failures.pop(id_empresa, None)
            return contexto if contexto else None
        except Exception as e:
            failed_by_exception = True
            logger.warning(
                "[CONTEXTO_NEGOCIO] Error intento %d/%d id_empresa=%s: %s: %s",
                attempt + 1, max_retries, id_empresa, type(e).__name__, e
            )
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)

    # Solo incrementar circuit breaker cuando falló por excepción (red/timeout), no por success: false
    if failed_by_exception:
        current = _contexto_failures.get(id_empresa, 0)
        _contexto_failures[id_empresa] = current + 1
        logger.warning(
            "[CONTEXTO_NEGOCIO] Circuit breaker id_empresa=%s: fallos acumulados=%s/%s",
            id_empresa, current + 1, _contexto_failure_threshold
        )
    logger.warning(
        "[CONTEXTO_NEGOCIO] No se pudo obtener contexto id_empresa=%s tras %d intentos",
        id_empresa, max_retries
    )
    return None


__all__ = ["fetch_contexto_negocio"]
