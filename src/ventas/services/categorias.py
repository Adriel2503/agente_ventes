"""
Categorías desde ws_informacion_ia.php.
Usa codOpe: OBTENER_CATEGORIAS. Para inyectar en el system prompt (información de productos y servicios).
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

import httpx

try:
    from ..config import config as app_config
except ImportError:
    from ventas.config import config as app_config

logger = logging.getLogger(__name__)

COD_OPE = "OBTENER_CATEGORIAS"
MAX_ITEMS = 15


def _clean_text(text: Optional[str], max_chars: int = 200) -> str:
    if not text or not str(text).strip():
        return ""
    s = str(text).strip()
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&")
    s = re.sub(r"\s+", " ", s).strip()
    return (s[:max_chars] + "...") if len(s) > max_chars else s


def format_categorias_para_prompt(categorias: List[Dict[str, Any]]) -> str:
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


def obtener_categorias(id_empresa: int) -> str:
    """
    Obtiene categorías de la API (OBTENER_CATEGORIAS) y devuelve texto formateado
    para inyectar en el system prompt como información de productos y servicios.

    Args:
        id_empresa: ID de la empresa

    Returns:
        Texto formateado (nombre + descripción por ítem) o mensaje por defecto si falla/vacío.
    """
    payload = {
        "codOpe": COD_OPE,
        "id_empresa": id_empresa,
    }
    logger.debug("[CATEGORIAS] POST %s - %s", app_config.API_INFORMACION_URL, json.dumps(payload))

    try:
        with httpx.Client(timeout=app_config.API_TIMEOUT) as client:
            response = client.post(
                app_config.API_INFORMACION_URL,
                json=payload,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            response.raise_for_status()
            data = response.json()
    except Exception as e:
        logger.warning("[CATEGORIAS] Error al obtener categorías: %s", e)
        return "No hay información de productos y servicios cargada. Usa la herramienta search_productos_servicios cuando pregunten por algo concreto."

    if not data.get("success"):
        logger.warning("[CATEGORIAS] API no success: %s", data.get("error") or data.get("message"))
        return "No hay información de productos y servicios cargada. Usa la herramienta search_productos_servicios cuando pregunten por algo concreto."

    categorias = data.get("categorias", [])
    if not categorias:
        return "No hay información de productos y servicios cargada. Usa la herramienta search_productos_servicios cuando pregunten por algo concreto."

    return format_categorias_para_prompt(categorias)


__all__ = ["obtener_categorias", "format_categorias_para_prompt"]
