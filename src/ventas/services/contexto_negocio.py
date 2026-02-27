"""
Contexto de negocio: fetch desde API MaravIA para el system prompt.
Usa OBTENER_CONTEXTO_NEGOCIO (ws_informacion_ia.php).
Cache TTL + circuit breaker (vía resilient_call) + anti-thundering herd.
"""

import asyncio
from typing import Any

from cachetools import TTLCache

try:
    from ..logger import get_logger
    from .http_client import post_informacion
    from ._resilience import resilient_call
    from .circuit_breaker import informacion_cb
except ImportError:
    from ventas.logger import get_logger
    from ventas.services.http_client import post_informacion
    from ventas.services._resilience import resilient_call
    from ventas.services.circuit_breaker import informacion_cb

logger = get_logger(__name__)

# Cache TTL: mismo criterio que citas (max 500 empresas, 1 hora)
_contexto_cache: TTLCache = TTLCache(maxsize=500, ttl=3600)

# Lock por id_empresa para anti-thundering herd (patrón agent_citas).
_contexto_locks: dict[Any, asyncio.Lock] = {}


async def fetch_contexto_negocio(id_empresa: Any | None) -> str | None:
    """
    Obtiene el contexto de negocio desde la API para inyectar en el system prompt.
    Incluye cache TTL (1 h), circuit breaker (3 fallos → abierto 5 min vía informacion_cb),
    retry (tenacity en post_with_retry) y deduplicación via Lock (anti-thundering herd).

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
            id_empresa, len(contexto) if contexto else 0,
        )
        return contexto if contexto else None

    # 2. Circuit breaker (verificación rápida antes de tomar el lock)
    if informacion_cb.is_open(id_empresa):
        logger.warning("[CONTEXTO_NEGOCIO] Circuit abierto para id_empresa=%s", id_empresa)
        return None

    # 3. Anti-thundering herd: Lock por empresa + double-check post-lock.
    payload = {
        "codOpe": "OBTENER_CONTEXTO_NEGOCIO",
        "id_empresa": id_empresa,
    }
    lock = _contexto_locks.setdefault(id_empresa, asyncio.Lock())
    try:
        async with lock:
            # Double-check: otro request puede haber populado el cache mientras esperábamos
            if id_empresa in _contexto_cache:
                contexto = _contexto_cache[id_empresa]
                logger.debug("[CONTEXTO_NEGOCIO] Cache HIT (post-lock) id_empresa=%s", id_empresa)
                return contexto if contexto else None

            try:
                data = await resilient_call(
                    lambda: post_informacion(payload),
                    cb=informacion_cb,
                    circuit_key=id_empresa,
                    service_name="CONTEXTO_NEGOCIO",
                )
            except Exception as e:
                logger.warning(
                    "[CONTEXTO_NEGOCIO] No se pudo obtener contexto id_empresa=%s: %s",
                    id_empresa, e,
                )
                return None

            if not data.get("success"):
                logger.warning(
                    "[CONTEXTO_NEGOCIO] API sin éxito id_empresa=%s: %s",
                    id_empresa, data.get("error"),
                )
                return None

            contexto = data.get("contexto_negocio") or ""
            contexto = str(contexto).strip() if contexto else ""

            if contexto:
                logger.info(
                    "[CONTEXTO_NEGOCIO] Respuesta recibida id_empresa=%s, longitud=%s caracteres",
                    id_empresa, len(contexto),
                )
            else:
                logger.info("[CONTEXTO_NEGOCIO] Respuesta recibida id_empresa=%s, contexto vacío", id_empresa)

            _contexto_cache[id_empresa] = contexto
            return contexto if contexto else None
    finally:
        _contexto_locks.pop(id_empresa, None)


__all__ = ["fetch_contexto_negocio"]
