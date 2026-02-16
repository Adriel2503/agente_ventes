"""
Búsqueda de productos y servicios desde ws_informacion_ia.php.
Usa codOpe: BUSCAR_PRODUCTOS_SERVICIOS_VENTAS_DIRECTAS
"""

import json
import re
import logging
from typing import Any, Dict, List, Optional

import httpx

try:
    from ..config import config as app_config
except ImportError:
    from ventas.config import config as app_config

logger = logging.getLogger(__name__)

COD_OPE = "BUSCAR_PRODUCTOS_SERVICIOS_VENTAS_DIRECTAS"


def _clean_description(desc: Optional[str], max_chars: int = 120) -> str:
    """Limpia HTML y trunca la descripción."""
    if not desc or not str(desc).strip():
        return "-"
    text = str(desc).strip()
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = re.sub(r"\s+", " ", text).strip()
    return (text[:max_chars] + "...") if len(text) > max_chars else text


def _format_precio(precio: Any) -> str:
    if precio is None or precio == "":
        return "-"
    try:
        return f"S/. {float(precio):,.2f}"
    except (TypeError, ValueError):
        return "-"


def _format_item(p: Dict[str, Any]) -> List[str]:
    nombre = (p.get("nombre") or "-").strip()
    precio_str = _format_precio(p.get("precio_unitario"))
    categoria = (p.get("nombre_categoria") or "-").strip()
    descripcion = _clean_description(p.get("descripcion"))
    tipo = (p.get("nombre_tipo_producto") or "").strip().lower()
    es_servicio = tipo == "servicio"
    unidad = "sesión" if es_servicio else (
        (p.get("nombre_unidad") or "unidad").strip().lower()
    )
    lineas = [
        f"### {nombre}",
        f"- **Precio:** {precio_str} por {unidad}",
        f"- **Categoría:** {categoria}",
        f"- **Descripción:** {descripcion}",
        "",
    ]
    return lineas


def format_productos_para_respuesta(productos: List[Dict[str, Any]]) -> str:
    """Formatea la lista de productos/servicios para la respuesta de la tool."""
    if not productos:
        return "No se encontraron resultados."
    lineas = []
    for p in productos:
        lineas.extend(_format_item(p))
    return "\n".join(lineas).strip()


async def buscar_productos_servicios(
    id_empresa: int,
    busqueda: str,
    limite: int = 10,
    log_search_apis: bool = False,
) -> Dict[str, Any]:
    """
    Busca productos y servicios por término (ventas directas).

    Args:
        id_empresa: ID de la empresa
        busqueda: Término de búsqueda
        limite: Cantidad máxima de resultados (default 10)
        log_search_apis: Si True, registra API, URL, payload y respuesta en info

    Returns:
        Dict con success, productos (lista), error si aplica
    """
    if not busqueda or not str(busqueda).strip():
        return {"success": False, "productos": [], "error": "El término de búsqueda no puede estar vacío"}

    payload = {
        "codOpe": COD_OPE,
        "id_empresa": id_empresa,
        "busqueda": str(busqueda).strip(),
        "limite": limite,
    }
    if log_search_apis:
        logger.info("[search_productos_servicios] API: ws_informacion_ia.php - %s", COD_OPE)
        logger.info("  URL: %s", app_config.API_INFORMACION_URL)
        logger.info("  Enviado: %s", json.dumps(payload, ensure_ascii=False))
    logger.debug(
        "[BUSQUEDA] POST %s - %s",
        app_config.API_INFORMACION_URL,
        json.dumps(payload, ensure_ascii=False),
    )

    try:
        async with httpx.AsyncClient(timeout=app_config.API_TIMEOUT) as client:
            response = await client.post(
                app_config.API_INFORMACION_URL,
                json=payload,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            response.raise_for_status()
            data = response.json()

        if log_search_apis:
            logger.info("  Respuesta: %s", json.dumps(data, ensure_ascii=False))
        if not data.get("success"):
            error_msg = data.get("error") or data.get("message") or "Error desconocido"
            logger.warning("[BUSQUEDA] API no success: %s", error_msg)
            return {"success": False, "productos": [], "error": error_msg}

        productos = data.get("productos", [])
        return {"success": True, "productos": productos, "error": None}

    except httpx.TimeoutException:
        logger.warning("[BUSQUEDA] Timeout al buscar productos")
        return {"success": False, "productos": [], "error": "La búsqueda tardó demasiado. Intenta de nuevo."}
    except httpx.RequestError as e:
        logger.warning("[BUSQUEDA] Error de conexión: %s", e)
        return {"success": False, "productos": [], "error": str(e)}
    except Exception as e:
        logger.exception("[BUSQUEDA] Error inesperado: %s", e)
        return {"success": False, "productos": [], "error": str(e)}


__all__ = ["buscar_productos_servicios", "format_productos_para_respuesta"]
