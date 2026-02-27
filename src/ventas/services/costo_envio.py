"""
Costos de envío por zona desde ws_informacion_ia.php.
Usa codOpe: OBTENER_COSTO_ENVIO. Para inyectar en el system prompt (envío a domicilio).

Respuesta de la API:
  { "success": true, "zonas_costos": "<JSON string>" }

El campo zonas_costos es un JSON string que al parsear tiene la forma:
  { "zonas": [ { "lugar": "...", "costo": "...", "tipo_envio": "...", "tiempo_entrega": "..." }, ... ] }

Los 4 campos de cada zona son editables por el usuario del negocio (texto libre).
"""

import json
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

COD_OPE = "OBTENER_COSTO_ENVIO"

# Cache TTL 1h (mismo criterio que categorías, sucursales, métodos de pago)
_costo_envio_cache: TTLCache = TTLCache(maxsize=500, ttl=3600)


def _norm(s: Any) -> str:
    """Normaliza a string; None/vacío -> '-'."""
    if s is None:
        return "-"
    v = str(s).strip()
    return v if v else "-"


def _format_costo(costo: Any) -> str:
    """Formatea el costo como 'S/ X' o 'S/ X.XX'; si no es numérico, muestra el valor original."""
    if costo is None or str(costo).strip() == "":
        return "-"
    try:
        valor = float(str(costo).strip())
        if valor == int(valor):
            return f"S/ {int(valor)}"
        return f"S/ {valor:.2f}"
    except (ValueError, TypeError):
        return str(costo).strip()


def format_costos_envio_para_prompt(zonas: list[dict[str, Any]]) -> str:
    """
    Formatea la lista de zonas de envío para inyectar en el system prompt.
    Salida (una línea por zona):
      - Zona: San Isidro — Costo: S/ 20, Tipo: Delivery, Tiempo: 4 dias
      - Zona: Molina — Costo: S/ 50, Tipo: lento, Tiempo: 10 dias
    """
    if not zonas:
        return ""

    lineas = []
    for zona in zonas:
        lugar = _norm(zona.get("lugar"))
        costo = _format_costo(zona.get("costo"))
        tipo = _norm(zona.get("tipo_envio"))
        tiempo = _norm(zona.get("tiempo_entrega"))
        lineas.append(f"- Zona: {lugar} — Costo: {costo}, Tipo: {tipo}, Tiempo: {tiempo}")

    return "\n".join(lineas)


async def obtener_costos_envio(id_empresa: int) -> str:
    """
    Obtiene los costos de envío por zona de la API (OBTENER_COSTO_ENVIO) y devuelve
    texto formateado para inyectar en el system prompt.
    Incluye cache TTL 1h para evitar llamadas repetidas durante la vida del agente.

    Args:
        id_empresa: ID de la empresa

    Returns:
        Texto formateado con una línea por zona, o '' si no hay zonas / falla la API.
        El template usa | default('...') para mostrar un mensaje cuando el resultado es ''.
    """
    if id_empresa in _costo_envio_cache:
        logger.debug("[COSTO_ENVIO] Cache HIT id_empresa=%s", id_empresa)
        return _costo_envio_cache[id_empresa]

    payload = {"codOpe": COD_OPE, "id_empresa": id_empresa}

    try:
        data = await resilient_call(
            lambda: post_informacion(payload),
            cb=informacion_cb,
            circuit_key=id_empresa,
            service_name="COSTO_ENVIO",
        )
    except Exception as e:
        logger.warning("[COSTO_ENVIO] No se pudo obtener costos de envío id_empresa=%s: %s", id_empresa, e)
        return ""

    if not data.get("success"):
        logger.warning(
            "[COSTO_ENVIO] API no success id_empresa=%s: %s",
            id_empresa, data.get("error") or data.get("message"),
        )
        return ""

    zonas_costos_raw = data.get("zonas_costos")
    if not zonas_costos_raw:
        logger.debug("[COSTO_ENVIO] Sin zonas_costos id_empresa=%s", id_empresa)
        return ""

    try:
        zonas_obj = json.loads(zonas_costos_raw)
        zonas = zonas_obj.get("zonas", [])
    except (json.JSONDecodeError, AttributeError, TypeError) as e:
        logger.warning("[COSTO_ENVIO] Error parseando zonas_costos id_empresa=%s: %s", id_empresa, e)
        return ""

    if not isinstance(zonas, list) or not zonas:
        logger.debug("[COSTO_ENVIO] Zonas vacías id_empresa=%s", id_empresa)
        return ""

    resultado = format_costos_envio_para_prompt(zonas)
    _costo_envio_cache[id_empresa] = resultado
    logger.debug("[COSTO_ENVIO] Cache SET id_empresa=%s (%s zonas)", id_empresa, len(zonas))
    return resultado


__all__ = ["obtener_costos_envio", "format_costos_envio_para_prompt"]
