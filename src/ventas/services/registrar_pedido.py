"""
Registro de pedido en ws_informacion_ia.php.
Usa codOpe: REGISTRAR_PEDIDO.

IMPORTANTE: Esta operación es ESCRITURA. No se usa post_with_retry ni retry
automático para evitar registros duplicados si el servidor recibió la request
pero la respuesta timeouteó. Se hace un único POST directo con el cliente compartido.

Campos del payload:
  Fijos (código):     codOpe, id_moneda (1)
  Contexto (runtime): id_empresa, id_prospecto (= session_id)
  IA (argumentos):    productos, operacion, modalidad, tipo_envio, direccion,
                      costo_envio, observacion, fecha_entrega_estimada, nombre,
                      dni, celular, email, medio_pago, sucursal, monto_pagado
"""

from typing import Any

try:
    from .. import config as app_config
    from ..logger import get_logger
    from ..services.http_client import get_client
except ImportError:
    from ventas import config as app_config
    from ventas.logger import get_logger
    from ventas.services.http_client import get_client

logger = get_logger(__name__)

COD_OPE = "REGISTRAR_PEDIDO"
ID_MONEDA_DEFAULT = 1


async def registrar_pedido(
    id_empresa: int,
    id_prospecto: int,
    productos: list[dict[str, Any]],
    operacion: str,
    modalidad: str,
    tipo_envio: str,
    nombre: str,
    dni: str,
    celular: str,
    medio_pago: str,
    monto_pagado: float,
    direccion: str = "",
    costo_envio: float = 0,
    observacion: str = "",
    fecha_entrega_estimada: str = "",
    email: str = "",
    sucursal: str = "",
) -> str:
    """
    Registra el pedido en la API de MaravIA (REGISTRAR_PEDIDO).

    Hace un único POST sin retry (escritura: riesgo de duplicados si se reintenta).

    Args:
        id_empresa:             ID de la empresa (del contexto del agente).
        id_prospecto:           ID del prospecto/cliente (= session_id del agente).
        productos:              Lista de { "id_catalogo": <int>, "cantidad": <int> }.
        operacion:              Número/código de operación de la transacción (Yape, BCP, etc.).
        modalidad:              "Delivery" o "Recojo".
        tipo_envio:             Tipo de envío acordado (ej. "rapidito", "Recojo").
        nombre:                 Nombre completo del cliente.
        dni:                    DNI del cliente.
        celular:                Teléfono del cliente.
        medio_pago:             Medio de pago (ej. "yape", "transferencia").
        monto_pagado:           Monto pagado por el cliente.
        direccion:              Dirección de entrega (vacío para recojo en tienda).
        costo_envio:            Costo de envío acordado (0 si recojo).
        observacion:            Observación u nota adicional (opcional).
        fecha_entrega_estimada: Fecha estimada de entrega (ej. "2026-03-05").
        email:                  Correo electrónico del cliente (opcional).
        sucursal:               Sucursal de recojo (vacío si delivery).

    Returns:
        String con mensaje de éxito o de error para que la tool lo devuelva al agente.
    """
    payload: dict[str, Any] = {
        "codOpe": COD_OPE,
        "id_empresa": id_empresa,
        "id_moneda": ID_MONEDA_DEFAULT,
        "id_prospecto": id_prospecto,
        "productos": productos,
        "operacion": operacion,
        "modalidad": modalidad,
        "tipo_envio": tipo_envio,
        "direccion": direccion,
        "costo_envio": costo_envio,
        "observacion": observacion,
        "fecha_entrega_estimada": fecha_entrega_estimada,
        "nombre": nombre,
        "dni": dni,
        "celular": celular,
        "email": email,
        "medio_pago": medio_pago,
        "sucursal": sucursal,
        "monto_pagado": monto_pagado,
    }

    logger.info(
        "[REGISTRAR_PEDIDO] POST id_empresa=%s id_prospecto=%s productos=%s operacion=%s",
        id_empresa, id_prospecto, productos, operacion,
    )

    try:
        client = get_client()
        response = await client.post(app_config.API_INFORMACION_URL, json=payload)
        response.raise_for_status()
        data: dict[str, Any] = response.json()
    except Exception as e:
        logger.error("[REGISTRAR_PEDIDO] Error en POST id_empresa=%s: %s", id_empresa, e, exc_info=True)
        return f"No se pudo registrar el pedido por un error de comunicación: {type(e).__name__}. Por favor, intenta nuevamente."

    if not data.get("success"):
        msg_error = data.get("error") or data.get("message") or "respuesta inesperada de la API"
        logger.warning("[REGISTRAR_PEDIDO] API no success id_empresa=%s: %s", id_empresa, msg_error)
        return f"No se pudo registrar el pedido: {msg_error}"

    id_pedido = data.get("id_pedido") or data.get("id") or ""
    logger.info("[REGISTRAR_PEDIDO] Pedido registrado OK id_empresa=%s id_pedido=%s", id_empresa, id_pedido)

    if id_pedido:
        return f"Pedido registrado exitosamente. Número de pedido: {id_pedido}."
    return "Pedido registrado exitosamente."


__all__ = ["registrar_pedido"]
