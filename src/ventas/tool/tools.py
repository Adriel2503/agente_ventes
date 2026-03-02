"""
Tools del agente de ventas.
Incluye:
  - search_productos_servicios: búsqueda en catálogo vía BUSCAR_PRODUCTOS_SERVICIOS_VENTAS_DIRECTAS
  - registrar_pedido_delivery: registra pedido con envío a domicilio
  - registrar_pedido_sucursal: registra pedido con recojo en sucursal
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
async def registrar_pedido_delivery(
    productos: list[ProductoItem],
    operacion: int,
    tipo_envio: str,
    direccion: str,
    costo_envio: float,
    fecha_entrega_estimada: str,
    nombre: str,
    dni: int,
    celular: int,
    medio_pago: str,
    monto_pagado: float,
    email: str,
    observacion: str = "",
    runtime: ToolRuntime = None,
) -> str:
    """
    Registra un pedido con envío a domicilio (Delivery).

    Úsala SOLO cuando el cliente eligió DELIVERY y tienes todos estos datos:
    productos confirmados, número de operación del comprobante, tipo y costo
    de envío según la zona, dirección de entrega, y datos del cliente.

    IMPORTANTE: No inventes IDs de producto. El id_catalogo debe ser el ID
    que devolvió search_productos_servicios. Si falta algún dato, pídelo
    al cliente antes de llamar esta herramienta.

    Args:
        productos:              Lista de {"id_catalogo": int, "cantidad": int}.
        operacion:              Número de operación del comprobante (entero).
        tipo_envio:             Valor "Tipo" de la zona elegida en el bloque
                                "Costos de envío por zona" del system prompt
                                (ej. "Express", "Normal").
        direccion:              Dirección exacta de entrega del cliente.
        costo_envio:            Valor numérico "Costo" de la zona elegida.
        fecha_entrega_estimada: Fecha de entrega estimada en formato YYYY-MM-DD
                                (hoy + "Tiempo" de la zona elegida).
        nombre:                 Nombre completo del cliente.
        dni:                    DNI del cliente (entero).
        celular:                Teléfono del cliente (entero).
        medio_pago:             Medio de pago usado (ej. "yape", "transferencia").
        monto_pagado:           Monto pagado por el cliente.
        email:                  Correo del cliente.
        observacion:            Nota adicional (opcional).
        runtime:                Contexto automático inyectado por LangChain.

    Returns:
        Mensaje de éxito con número de pedido, o mensaje de error.
    """
    logger.debug(
        "[TOOL] registrar_pedido_delivery - productos=%s operacion=%s direccion=%s",
        productos, operacion, direccion,
    )

    ctx = runtime.context if runtime else None
    if not ctx or getattr(ctx, "id_empresa", None) is None:
        logger.warning("[TOOL] registrar_pedido_delivery - llamada sin contexto de empresa")
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
            modalidad="Delivery",
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
        )
    except Exception as e:
        _tool_status = "error"
        logger.error(
            "[TOOL] registrar_pedido_delivery - %s: %s (id_empresa=%s, operacion=%r)",
            type(e).__name__,
            e,
            id_empresa,
            operacion,
            exc_info=True,
        )
        return f"Error al registrar el pedido: {str(e)}. Intenta de nuevo."

    finally:
        TOOL_CALLS.labels(tool="registrar_pedido_delivery", status=_tool_status).inc()


@tool
async def registrar_pedido_sucursal(
    productos: list[ProductoItem],
    operacion: int,
    sucursal: str,
    nombre: str,
    dni: int,
    celular: int,
    medio_pago: str,
    monto_pagado: float,
    email: str,
    observacion: str = "",
    runtime: ToolRuntime = None,
) -> str:
    """
    Registra un pedido con recojo en sucursal.

    Úsala SOLO cuando el cliente eligió RECOGER EN SUCURSAL y tienes todos
    estos datos: productos confirmados, número de operación del comprobante,
    nombre exacto de la sucursal elegida, y datos del cliente.

    IMPORTANTE: No inventes IDs de producto. El id_catalogo debe ser el ID
    que devolvió search_productos_servicios. El nombre de la sucursal debe
    ser exactamente el que aparece en el bloque "Sucursales" del system prompt.
    Si falta algún dato, pídelo al cliente antes de llamar esta herramienta.

    Args:
        productos:   Lista de {"id_catalogo": int, "cantidad": int}.
        operacion:   Número de operación del comprobante (entero).
        sucursal:    Nombre exacto de la sucursal elegida (del bloque
                     "Sucursales (para recojo en tienda)" del system prompt).
        nombre:      Nombre completo del cliente.
        dni:         DNI del cliente (entero).
        celular:     Teléfono del cliente (entero).
        medio_pago:  Medio de pago usado (ej. "yape", "transferencia").
        monto_pagado: Monto pagado por el cliente.
        email:       Correo del cliente.
        observacion: Nota adicional (opcional).
        runtime:     Contexto automático inyectado por LangChain.

    Returns:
        Mensaje de éxito con número de pedido, o mensaje de error.
    """
    logger.debug(
        "[TOOL] registrar_pedido_sucursal - productos=%s operacion=%s sucursal=%s",
        productos, operacion, sucursal,
    )

    ctx = runtime.context if runtime else None
    if not ctx or getattr(ctx, "id_empresa", None) is None:
        logger.warning("[TOOL] registrar_pedido_sucursal - llamada sin contexto de empresa")
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
            modalidad="Sucursal",
            nombre=nombre,
            dni=dni,
            celular=celular,
            medio_pago=medio_pago,
            monto_pagado=monto_pagado,
            observacion=observacion,
            email=email,
            sucursal=sucursal,
        )
    except Exception as e:
        _tool_status = "error"
        logger.error(
            "[TOOL] registrar_pedido_sucursal - %s: %s (id_empresa=%s, operacion=%r)",
            type(e).__name__,
            e,
            id_empresa,
            operacion,
            exc_info=True,
        )
        return f"Error al registrar el pedido: {str(e)}. Intenta de nuevo."

    finally:
        TOOL_CALLS.labels(tool="registrar_pedido_sucursal", status=_tool_status).inc()


AGENT_TOOLS = [search_productos_servicios, registrar_pedido_delivery, registrar_pedido_sucursal]

__all__ = [
    "search_productos_servicios",
    "registrar_pedido_delivery",
    "registrar_pedido_sucursal",
    "AGENT_TOOLS",
]
