"""
Métodos de pago desde ws_informacion_ia.php.
Usa codOpe: OBTENER_METODOS_PAGO. Para inyectar en el system prompt (medios de pago).
"""

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

COD_OPE = "OBTENER_METODOS_PAGO"

# Cache TTL 1h (mismo criterio que contexto_negocio y preguntas_frecuentes)
_metodos_pago_cache: TTLCache = TTLCache(maxsize=500, ttl=3600)


def _norm(s: Any) -> str:
    """Normaliza a string; None/vacío -> ''."""
    if s is None:
        return ""
    return str(s).strip()


def _format_metodos_pago_para_prompt(metodos_pago: dict[str, Any]) -> str:
    """
    Formatea metodos_pago para el system prompt.
    Bancos: 1) nombre: Cuenta X, CCI Y. Billeteras digitales: 1) Yape: titular, celular. Si null/empty: "No hay ..."
    """
    if not metodos_pago:
        return "Bancos: No hay cuentas bancarias configuradas.\nBilleteras digitales: No hay billeteras digitales configuradas."

    lineas = []

    # Bancos
    bancos = metodos_pago.get("bancos") or []
    if bancos:
        bancos_lineas = []
        for i, b in enumerate(bancos, 1):
            nombre = _norm(b.get("nombre")) or "Banco"
            cuenta = _norm(b.get("numero_cuenta"))
            cci = _norm(b.get("cci"))
            parte = f"{i}) {nombre}: Cuenta {cuenta}, CCI {cci}" if cuenta or cci else f"{i}) {nombre}"
            bancos_lineas.append(parte)
        lineas.append("Bancos:\n" + "\n".join(bancos_lineas))
    else:
        lineas.append("Bancos: No hay cuentas bancarias configuradas.")

    # Billeteras digitales (yape, plin)
    wallets = []
    yape = metodos_pago.get("yape")
    if yape and isinstance(yape, dict):
        titular = _norm(yape.get("titular"))
        celular = _norm(yape.get("celular"))
        if titular or celular:
            partes = [p for p in [titular, f"celular {celular}" if celular else ""] if p]
            wallets.append("Yape: " + ", ".join(partes))
    plin = metodos_pago.get("plin")
    if plin and isinstance(plin, dict):
        titular = _norm(plin.get("titular"))
        celular = _norm(plin.get("celular"))
        if titular or celular:
            partes = [p for p in [titular, f"celular {celular}" if celular else ""] if p]
            wallets.append("Plin: " + ", ".join(partes))

    if wallets:
        billeteras_lineas = [f"{i}) {w}" for i, w in enumerate(wallets, 1)]
        lineas.append("Billeteras digitales:\n" + "\n".join(billeteras_lineas))
    else:
        lineas.append("Billeteras digitales: No hay billeteras digitales configuradas.")

    return "\n\n".join(lineas)


async def obtener_metodos_pago(id_empresa: int) -> str:
    """
    Obtiene métodos de pago de la API (OBTENER_METODOS_PAGO) y devuelve texto
    formateado para inyectar en el system prompt.
    Incluye cache TTL 1h para evitar llamadas repetidas durante la vida del agente.

    Args:
        id_empresa: ID de la empresa

    Returns:
        Texto formateado (Bancos + Billeteras digitales) o string vacío si falla.
    """
    if id_empresa in _metodos_pago_cache:
        logger.debug("[METODOS_PAGO] Cache HIT id_empresa=%s", id_empresa)
        return _metodos_pago_cache[id_empresa]

    payload = {"codOpe": COD_OPE, "id_empresa": id_empresa}

    try:
        data = await resilient_call(
            lambda: post_informacion(payload),
            cb=informacion_cb,
            circuit_key=id_empresa,
            service_name="METODOS_PAGO",
        )
    except Exception as e:
        logger.warning("[METODOS_PAGO] No se pudo obtener métodos de pago id_empresa=%s: %s", id_empresa, e)
        return ""

    if not data.get("success"):
        logger.warning(
            "[METODOS_PAGO] API no success id_empresa=%s: %s",
            id_empresa, data.get("error") or data.get("message"),
        )
        return ""

    metodos_pago = data.get("metodos_pago")
    if not metodos_pago or not isinstance(metodos_pago, dict):
        return ""

    resultado = _format_metodos_pago_para_prompt(metodos_pago)
    _metodos_pago_cache[id_empresa] = resultado
    logger.debug("[METODOS_PAGO] Cache SET id_empresa=%s", id_empresa)
    return resultado


__all__ = ["obtener_metodos_pago"]
