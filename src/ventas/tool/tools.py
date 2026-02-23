"""
Tools del agente de ventas.
Versión mínima: búsqueda de productos/servicios (BUSCAR_PRODUCTOS_SERVICIOS_VENTAS_DIRECTAS).
"""

import logging

from langchain.tools import tool, ToolRuntime

try:
    from ..metrics import TOOL_CALLS
    from ..services.busqueda_productos import buscar_productos_servicios, format_productos_para_respuesta
except ImportError:
    from ventas.metrics import TOOL_CALLS
    from ventas.services.busqueda_productos import buscar_productos_servicios, format_productos_para_respuesta

logger = logging.getLogger(__name__)


@tool
async def search_productos_servicios(
    busqueda: str,
    limite: int = 10,
    runtime: ToolRuntime = None
) -> str:
    """
    Busca productos y servicios del catálogo por nombre o descripción (ventas directas).
    Úsala cuando el cliente pregunte por precios, descripción o detalles de un producto o servicio.

    Args:
        busqueda: Término de búsqueda (ej: "Juego", "laptop", "consulta")
        limite: Cantidad máxima de resultados (opcional, default 10)
        runtime: Contexto automático (inyectado por LangChain)

    Returns:
        Texto con los productos/servicios encontrados (precio, categoría, descripción)
    """
    logger.debug("[TOOL] search_productos_servicios - busqueda: %s, limite: %s", busqueda, limite)

    ctx = runtime.context if runtime else None
    if not ctx or getattr(ctx, "id_empresa", None) is None:
        logger.warning("[TOOL] search_productos_servicios - llamada sin contexto de empresa")
        return "No tengo el contexto de empresa para buscar productos; no puedo mostrar el catálogo en este momento."
    id_empresa = ctx.id_empresa

    _tool_status = "ok"
    try:
        result = await buscar_productos_servicios(
            id_empresa=id_empresa,
            busqueda=busqueda,
            limite=limite,
            log_search_apis=True,
        )

        if not result["success"]:
            return result.get("error", "No se pudo completar la búsqueda.")

        productos = result.get("productos", [])
        if not productos:
            return f"No encontré productos o servicios que coincidan con '{busqueda}'. Prueba con otros términos."

        lineas = [f"Encontré {len(productos)} resultado(s) para '{busqueda}':\n"]
        lineas.append(format_productos_para_respuesta(productos))
        return "\n".join(lineas)

    except Exception as e:
        _tool_status = "error"
        logger.error(
            "[TOOL] search_productos_servicios - %s: %s (busqueda=%r, id_empresa=%s)",
            type(e).__name__,
            e,
            busqueda,
            id_empresa,
            exc_info=True,
        )
        return f"Error al buscar: {str(e)}. Intenta de nuevo."

    finally:
        TOOL_CALLS.labels(tool="search_productos_servicios", status=_tool_status).inc()


AGENT_TOOLS = [search_productos_servicios]

__all__ = ["search_productos_servicios", "AGENT_TOOLS"]
