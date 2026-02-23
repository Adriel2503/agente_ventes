"""
Preguntas frecuentes: fetch desde API MaravIA (ws_preguntas_frecuentes.php) para el system prompt.
Formato Pregunta/Respuesta para que el modelo entienda y use las FAQs.
"""

import logging
from typing import Any

import httpx
from cachetools import TTLCache

try:
    from .. import config as app_config
    from .api_informacion import get_client
except ImportError:
    from ventas import config as app_config
    from ventas.services.api_informacion import get_client

logger = logging.getLogger(__name__)

# Cache TTL por id_chatbot (1 hora), mismo criterio que contexto_negocio
_preguntas_cache: TTLCache = TTLCache(maxsize=500, ttl=3600)


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

    payload = {"id_chatbot": id_chatbot}
    try:
        logger.debug("[PREGUNTAS_FRECUENTES] Obteniendo FAQs id_chatbot=%s", id_chatbot)
        client = get_client()
        response = await client.post(
            app_config.API_PREGUNTAS_FRECUENTES_URL,
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
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
    except (httpx.TimeoutException, httpx.RequestError, httpx.HTTPStatusError) as e:
        logger.warning("[PREGUNTAS_FRECUENTES] Error de red id_chatbot=%s: %s", id_chatbot, e)
        return ""
    except Exception as e:
        logger.error("[PREGUNTAS_FRECUENTES] Error inesperado id_chatbot=%s: %s", id_chatbot, e, exc_info=True)
        return ""


__all__ = ["fetch_preguntas_frecuentes", "format_preguntas_frecuentes_para_prompt"]
