"""
Preguntas frecuentes: fetch desde API MaravIA (ws_preguntas_frecuentes.php) para el system prompt.
Formato Pregunta/Respuesta para que el modelo entienda y use las FAQs.
"""

import asyncio
from typing import Any

from cachetools import TTLCache

try:
    from .. import config as app_config
    from ..logger import get_logger
    from .http_client import get_client
    from ._resilience import resilient_call
    from .circuit_breaker import preguntas_cb
except ImportError:
    from ventas import config as app_config
    from ventas.logger import get_logger
    from ventas.services.http_client import get_client
    from ventas.services._resilience import resilient_call
    from ventas.services.circuit_breaker import preguntas_cb

logger = get_logger(__name__)

# Cache TTL por id_chatbot (1 hora), mismo criterio que contexto_negocio
_preguntas_cache: TTLCache = TTLCache(maxsize=500, ttl=3600)

# Lock por id_chatbot para evitar thundering herd (mismo patrón que horario_cache en citas).
_fetch_locks: dict[Any, asyncio.Lock] = {}


def format_preguntas_frecuentes_para_prompt(items: list[dict[str, Any]]) -> str:
    """
    Formatea la lista de FAQs (solo pregunta y respuesta) para inyectar en el system prompt.
    Usa etiquetas "Pregunta:" y "Respuesta:" para que el modelo entienda el formato.

    Args:
        items: Lista de dicts con "pregunta" y "respuesta".

    Returns:
        String listo para el system prompt.
    """
    if not items:
        return ""

    lineas = []
    for item in items:
        pregunta = (item.get("pregunta") or "").strip()
        respuesta = (item.get("respuesta") or "").strip()
        if not pregunta and not respuesta:
            continue
        lineas.append(f"Pregunta: {pregunta or '(sin texto)'}")
        lineas.append(f"Respuesta: {respuesta or '(sin texto)'}")
        lineas.append("")

    return "\n".join(lineas).strip() if lineas else ""


async def fetch_preguntas_frecuentes(id_chatbot: Any | None) -> str:
    """
    Obtiene las preguntas frecuentes desde la API para inyectar en el system prompt.
    Usa cache TTL por id_chatbot. Body: {"id_chatbot": id_chatbot}.

    Args:
        id_chatbot: ID del chatbot (int o str). Si es None o vacío, retorna "".

    Returns:
        String formateado (Pregunta:/Respuesta:) o "" si no hay datos o falla.
    """
    if id_chatbot is None or id_chatbot == "":
        return ""

    # Cache
    if id_chatbot in _preguntas_cache:
        cached = _preguntas_cache[id_chatbot]
        logger.debug(
            "[PREGUNTAS_FRECUENTES] Cache HIT id_chatbot=%s (%s)",
            id_chatbot,
            "vacío" if not cached else "presente",
        )
        return cached if cached else ""

    # Fast reject: evita adquirir el lock cuando el circuito está abierto
    if preguntas_cb.is_open(id_chatbot):
        return ""

    # Serializar fetch por id_chatbot (thundering herd prevention)
    lock = _fetch_locks.setdefault(id_chatbot, asyncio.Lock())
    async with lock:
        # Double-check: otra coroutine pudo llenar el cache mientras esperábamos
        if id_chatbot in _preguntas_cache:
            cached = _preguntas_cache[id_chatbot]
            return cached if cached else ""

        payload = {"id_chatbot": id_chatbot}

        async def _fetch() -> dict:
            logger.debug("[PREGUNTAS_FRECUENTES] Obteniendo FAQs id_chatbot=%s", id_chatbot)
            client = get_client()
            response = await client.post(
                app_config.API_PREGUNTAS_FRECUENTES_URL,
                json=payload,
            )
            response.raise_for_status()
            return response.json()

        try:
            data = await resilient_call(
                _fetch,
                cb=preguntas_cb,
                circuit_key=id_chatbot,
                service_name="PREGUNTAS_FRECUENTES",
            )
            if not data.get("success"):
                logger.warning("[PREGUNTAS_FRECUENTES] API sin éxito id_chatbot=%s: %s", id_chatbot, data.get("error"))
                return ""
            items = data.get("preguntas_frecuentes") or []
            if not items:
                logger.info("[PREGUNTAS_FRECUENTES] Respuesta recibida id_chatbot=%s, sin preguntas", id_chatbot)
                return ""
            logger.info("[PREGUNTAS_FRECUENTES] Respuesta recibida id_chatbot=%s, %s preguntas", id_chatbot, len(items))
            formatted = format_preguntas_frecuentes_para_prompt(items)
            _preguntas_cache[id_chatbot] = formatted
            return formatted
        except Exception as e:
            logger.warning("[PREGUNTAS_FRECUENTES] No se pudo obtener FAQs id_chatbot=%s: %s", id_chatbot, e)
            return ""
        finally:
            _fetch_locks.pop(id_chatbot, None)


__all__ = ["fetch_preguntas_frecuentes", "format_preguntas_frecuentes_para_prompt"]
