"""
Tools del agente de ventas.
Incluye search_productos_servicios (búsqueda en catálogo vía BUSCAR_PRODUCTOS_SERVICIOS_VENTAS_DIRECTAS)
y registrar_pedido (registro del pedido confirmado vía REGISTRAR_PEDIDO).
"""

from typing import Any, TypedDict

from langchain.tools import tool, ToolRuntime

try:
    from ..logger import get_logger
    from ..metrics import TOOL_CALLS
    from ..services.busqueda_productos import buscar_productos_servicios, format_productos_para_respuesta
    from ..services.registrar_pedido import registrar_pedido as _svc_registrar_pedido
except ImportError:
    from ventas.logger import get_logger
    from ventas.metrics import TOOL_CALLS
    from ventas.services.busqueda_productos import buscar_productos_servicios, format_productos_para_respuesta
    from ventas.services.registrar_pedido import registrar_pedido as _svc_registrar_pedido

logger = get_logger(__name__)


@tool
async def search_productos_servicios(
    busqueda: str,
    runtime: ToolRuntime = None
) -> str:
    """
    Busca productos y servicios del catálogo por nombre o descripción (ventas directas).
    Úsala cuando el cliente pregunte por precios, descripción o detalles de un producto o servicio.

    Args:
        busqueda: Término de búsqueda (ej: "Juego", "laptop", "consulta")
        runtime: Contexto automático (inyectado por LangChain)

    Returns:
        Texto con los productos/servicios encontrados (precio, categoría, descripción)
    """
    logger.debug("[TOOL] search_productos_servicios - busqueda: %s", busqueda)

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


class ProductoItem(TypedDict):
    """Item de producto para registrar en un pedido. id_catalogo y cantidad pueden llegar como int o str desde el LLM."""
    id_catalogo: int
    cantidad: int 


@tool
async def registrar_pedido(
    productos: list[ProductoItem],
    operacion: int,
    modalidad: str,
    tipo_envio: str,
    nombre: str,
    dni: int,
    celular: int,
    medio_pago: str,
    monto_pagado: float,
    direccion: str = "",
    costo_envio: float | int = 0,
    observacion: str = "",
    fecha_entrega_estimada: str = "",
    email: str = "",
    sucursal: str = "",
    runtime: ToolRuntime = None,
) -> str:
    """
    Registra el pedido del cliente en el sistema una vez confirmado.

    Úsala SOLO cuando el cliente haya confirmado el pedido Y tengas todos los datos
    obligatorios: productos elegidos, número de operación del comprobante, datos del
    cliente (nombre, DNI, celular) y datos de entrega o recojo.

    modalidad: "Delivery" si el cliente eligió delivery; "Sucursal" si eligió recoger
    en sucursal. No uses "Recojo".

    tipo_envio: En Delivery = valor "Tipo" de la zona elegida en el bloque "Costos de
    envío por zona" del system prompt (ej. "Express", "Normal", lo que configure el
    negocio). En Sucursal = "Sucursal".

    Por modo:
    - Delivery: direccion = a dónde enviar (pregunta al cliente); costo_envio =
      número del "Costo" de la zona elegida; fecha_entrega_estimada = hoy + "Tiempo"
      de esa zona (YYYY-MM-DD); sucursal = "".
    - Sucursal: sucursal = nombre de la sucursal elegida (bloque "Sucursales");
      direccion = ""; costo_envio = 0; fecha_entrega_estimada = "".

    Campos comunes: productos (id_catalogo de search_productos_servicios + cantidad),
    operacion (número del comprobante), nombre, dni, celular, medio_pago, monto_pagado.
    email y observacion opcionales.

    IMPORTANTE: No inventes IDs de producto. El id_catalogo debe ser el ID que apareció
    en la respuesta de search_productos_servicios. Si no tienes algún dato obligatorio,
    pídelo al cliente antes de llamar esta herramienta.

    Args:
        productos:              Lista de {"id_catalogo": int, "cantidad": int}.
        operacion:              Número de operación del comprobante (entero).
        modalidad:              "Delivery" o "Sucursal".
        tipo_envio:             En Delivery: "Tipo" de la zona elegida; en Sucursal: "Sucursal".
        nombre:                 Nombre completo del cliente.
        dni:                    DNI del cliente (entero).
        celular:                Teléfono del cliente (entero).
        medio_pago:             Medio de pago (ej. "yape").
        monto_pagado:           Monto pagado.
        direccion:              Dirección de entrega (Delivery); vacío si Sucursal.
        costo_envio:            Costo de envío (Delivery); 0 si Sucursal.
        observacion:            Nota adicional (opcional).
        fecha_entrega_estimada: Fecha YYYY-MM-DD (Delivery); vacío si Sucursal.
        email:                  Correo del cliente (opcional).
        sucursal:               Nombre sucursal (Sucursal); vacío si Delivery.
        runtime:                Contexto automático inyectado por LangChain.

    Returns:
        Mensaje de éxito con número de pedido, o mensaje de error.
    """
    logger.debug(
        "[TOOL] registrar_pedido - modalidad=%s productos=%s operacion=%s",
        modalidad, productos, operacion,
    )

    ctx = runtime.context if runtime else None
    if not ctx or getattr(ctx, "id_empresa", None) is None:
        logger.warning("[TOOL] registrar_pedido - llamada sin contexto de empresa")
        return "No tengo el contexto de empresa; no puedo registrar el pedido en este momento."

    id_empresa = ctx.id_empresa
    id_prospecto = getattr(ctx, "session_id", 0)

    _tool_status = "ok"
    try:
        return await _svc_registrar_pedido(
            id_empresa=id_empresa,
            id_prospecto=id_prospecto,
            productos=productos,
            operacion=operacion,
            modalidad=modalidad,
            tipo_envio=tipo_envio,
            nombre=nombre,
            dni=dni,
            celular=celular,
            medio_pago=medio_pago,
            monto_pagado=monto_pagado,
            direccion=direccion,
            costo_envio=costo_envio,
            observacion=observacion,
            fecha_entrega_estimada=fecha_entrega_estimada,
            email=email,
            sucursal=sucursal,
        )
    except Exception as e:
        _tool_status = "error"
        logger.error(
            "[TOOL] registrar_pedido - %s: %s (id_empresa=%s, operacion=%r)",
            type(e).__name__,
            e,
            id_empresa,
            operacion,
            exc_info=True,
        )
        return f"Error al registrar el pedido: {str(e)}. Intenta de nuevo."

    finally:
        TOOL_CALLS.labels(tool="registrar_pedido", status=_tool_status).inc()


AGENT_TOOLS = [search_productos_servicios, registrar_pedido]

__all__ = ["search_productos_servicios", "registrar_pedido", "AGENT_TOOLS"]
