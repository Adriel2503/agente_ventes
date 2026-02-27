"""
Sucursales desde ws_informacion_ia.php.
Usa codOpe: OBTENER_SUCURSALES_PUBLICAS. Para inyectar en el system prompt (recojo en tienda).
"""

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

COD_OPE = "OBTENER_SUCURSALES_PUBLICAS"
MAX_SUCURSALES = 5

# Cache TTL 1h (mismo criterio que contexto_negocio y preguntas_frecuentes)
_sucursales_cache: TTLCache = TTLCache(maxsize=500, ttl=3600)


def _norm(s: str | None) -> str:
    """Normaliza y limpia un string; vacío/None -> ''."""
    if s is None:
        return ""
    return str(s).strip()


def _is_cerrado(s: str) -> bool:
    """Considera cerrado si está vacío o contiene 'cerrado'."""
    t = s.lower()
    return not t or "cerrado" in t


def format_horario_compacto(
    horario_lunes: str | None = None,
    horario_martes: str | None = None,
    horario_miercoles: str | None = None,
    horario_jueves: str | None = None,
    horario_viernes: str | None = None,
    horario_sabado: str | None = None,
    horario_domingo: str | None = None,
) -> str:
    """
    Agrupa días consecutivos con el mismo horario.
    Lun-Vie iguales -> "Lun-Vie: X"; si Sáb distinto -> "Sáb: Y"; si Dom distinto -> "Dom: Z".
    """
    dias = [
        ("Lun", _norm(horario_lunes)),
        ("Mar", _norm(horario_martes)),
        ("Mie", _norm(horario_miercoles)),
        ("Jue", _norm(horario_jueves)),
        ("Vie", _norm(horario_viernes)),
        ("Sáb", _norm(horario_sabado)),
        ("Dom", _norm(horario_domingo)),
    ]

    grupos: list[tuple[str, str]] = []

    i = 0
    while i < len(dias):
        label, valor = dias[i]
        j = i + 1
        while j < len(dias) and dias[j][1] == valor:
            j += 1
        rango = f"{label}-{dias[j - 1][0]}" if j - 1 > i else label
        if _is_cerrado(valor):
            grupos.append((rango, "cerrado"))
        elif valor:
            grupos.append((rango, valor))
        i = j

    if not grupos:
        return ""
    return ", ".join(f"{rango} {h}" for rango, h in grupos)


def format_sucursales_para_prompt(sucursales: list[dict[str, Any]]) -> str:
    """
    Formatea la lista de sucursales para inyectar en el system prompt.
    Salida: "1) Nombre, Direccion. Horario: [compacto]\n2) ..."
    """
    if not sucursales:
        return ""

    lineas = []
    for i, s in enumerate(sucursales[:MAX_SUCURSALES], 1):
        nombre = _norm(s.get("nombre")) or "Sin nombre"
        direccion = _norm(s.get("direccion")) or ""
        horario = format_horario_compacto(
            horario_lunes=s.get("horario_lunes"),
            horario_martes=s.get("horario_martes"),
            horario_miercoles=s.get("horario_miercoles"),
            horario_jueves=s.get("horario_jueves"),
            horario_viernes=s.get("horario_viernes"),
            horario_sabado=s.get("horario_sabado"),
            horario_domingo=s.get("horario_domingo"),
        )
        parte = f"{i}) {nombre}"
        if direccion:
            parte += f", {direccion}"
        if horario:
            parte += f". Horario: {horario}"
        parte += "."
        lineas.append(parte)

    return "\n".join(lineas)


async def obtener_sucursales(id_empresa: int) -> str:
    """
    Obtiene sucursales de la API (OBTENER_SUCURSALES_PUBLICAS) y devuelve texto
    formateado para inyectar en el system prompt (recojo en tienda).
    Incluye cache TTL 1h para evitar llamadas repetidas durante la vida del agente.

    Args:
        id_empresa: ID de la empresa

    Returns:
        Texto formateado (nombre, dirección, horario compacto por sucursal)
        o string vacío si falla/vacío.
    """
    if id_empresa in _sucursales_cache:
        logger.debug("[SUCURSALES] Cache HIT id_empresa=%s", id_empresa)
        return _sucursales_cache[id_empresa]

    payload = {"codOpe": COD_OPE, "id_empresa": id_empresa}

    try:
        data = await resilient_call(
            lambda: post_informacion(payload),
            cb=informacion_cb,
            circuit_key=id_empresa,
            service_name="SUCURSALES",
        )
    except Exception as e:
        logger.warning("[SUCURSALES] No se pudo obtener sucursales id_empresa=%s: %s", id_empresa, e)
        return ""

    if not data.get("success"):
        logger.warning("[SUCURSALES] API no success id_empresa=%s: %s", id_empresa, data.get("error") or data.get("message"))
        return ""

    sucursales = data.get("sucursales", [])
    if not sucursales:
        return ""

    resultado = format_sucursales_para_prompt(sucursales)
    _sucursales_cache[id_empresa] = resultado
    logger.debug("[SUCURSALES] Cache SET id_empresa=%s (%s sucursales)", id_empresa, len(sucursales))
    return resultado


__all__ = ["obtener_sucursales", "format_sucursales_para_prompt", "format_horario_compacto"]
