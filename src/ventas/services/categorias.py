"""
Categorías desde ws_informacion_ia.php.
Usa codOpe: OBTENER_CATEGORIAS. Para inyectar en el system prompt (información de productos y servicios).
"""

import re
from typing import Any

from cachetools import TTLCache

try:
    from ..logger import get_logger
    from ..services.http_client import post_informacion
    from ..services._resilience import resilient_call
    from ..services.circuit_breaker import informacion_cb
except ImportError:
    from ventas.logger import get_logger
    from ventas.services.http_client import post_informacion
    from ventas.services._resilience import resilient_call
    from ventas.services.circuit_breaker import informacion_cb

logger = get_logger(__name__)

COD_OPE = "OBTENER_CATEGORIAS"
MAX_ITEMS = 15

# Cache TTL 1h (mismo criterio que contexto_negocio y preguntas_frecuentes)
_categorias_cache: TTLCache = TTLCache(maxsize=500, ttl=3600)


def _clean_text(text: str | None, max_chars: int = 200) -> str:
    if not text or not str(text).strip():
        return ""
    s = str(text).strip()
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&")
    s = re.sub(r"\s+", " ", s).strip()
    return (s[:max_chars] + "...") if len(s) > max_chars else s


def format_categorias_para_prompt(categorias: list[dict[str, Any]]) -> str:
    """
    Formatea la lista de categorías para inyectar en el system prompt.
    Salida: "1) Nombre: descripcion. (N productos)\n2) ..."
    cantidad_productos solo se añade si > 0.
    """
    if not categorias:
        return ""

    lineas = []
    for i, cat in enumerate(categorias[:MAX_ITEMS], 1):
        nombre = (cat.get("nombre") or "").strip() or "Sin nombre"
        desc = _clean_text(cat.get("descripcion"), max_chars=200)
        cantidad = cat.get("cantidad_productos")
        parte = f"{i}) {nombre}: {desc}." if desc else f"{i}) {nombre}."
        if cantidad is not None and int(cantidad) > 0:
            parte += f" ({int(cantidad)} productos)"
        lineas.append(parte)

    return "\n".join(lineas)


_DEFAULT_MSG = (
    "No hay información de productos y servicios cargada. "
    "Usa la herramienta search_productos_servicios cuando pregunten por algo concreto."
)


async def obtener_categorias(id_empresa: int) -> str:
    """
    Obtiene categorías de la API (OBTENER_CATEGORIAS) y devuelve texto formateado
    para inyectar en el system prompt como información de productos y servicios.
    Incluye cache TTL 1h para evitar llamadas repetidas durante la vida del agente.

    Args:
        id_empresa: ID de la empresa

    Returns:
        Texto formateado (nombre + descripción por ítem) o mensaje por defecto si falla/vacío.
    """
    if id_empresa in _categorias_cache:
        logger.debug("[CATEGORIAS] Cache HIT id_empresa=%s", id_empresa)
        return _categorias_cache[id_empresa]

    payload = {"codOpe": COD_OPE, "id_empresa": id_empresa}

    try:
        data = await resilient_call(
            lambda: post_informacion(payload),
            cb=informacion_cb,
            circuit_key=id_empresa,
            service_name="CATEGORIAS",
        )
    except Exception as e:
        logger.warning("[CATEGORIAS] No se pudo obtener categorías id_empresa=%s: %s", id_empresa, e)
        return _DEFAULT_MSG

    if not data.get("success"):
        logger.warning("[CATEGORIAS] API no success id_empresa=%s: %s", id_empresa, data.get("error") or data.get("message"))
        return _DEFAULT_MSG

    categorias = data.get("categorias", [])
    if not categorias:
        return _DEFAULT_MSG

    resultado = format_categorias_para_prompt(categorias)
    _categorias_cache[id_empresa] = resultado
    logger.debug("[CATEGORIAS] Cache SET id_empresa=%s (%s categorías)", id_empresa, len(categorias))
    return resultado


__all__ = ["obtener_categorias", "format_categorias_para_prompt"]
