"""
Búsqueda de productos y servicios desde ws_informacion_ia.php.
Usa codOpe: BUSCAR_PRODUCTOS_SERVICIOS_VENTAS_DIRECTAS

Resiliencia:
  - TTLCache 15 min por (id_empresa, búsqueda): absorbe búsquedas repetidas
    del mismo término entre usuarios de la misma empresa.
  - Anti-thundering herd: si N usuarios buscan el mismo término simultáneamente
    en cache miss, solo el primero llama a la API; los demás esperan ese Lock.
  - Retry: tenacity en post_with_retry (TransportError, exponential backoff).
  - Circuit breaker: informacion_cb compartido (3 fallos → abierto 5 min, auto-reset).
"""

import asyncio
import json
import logging
import re
from typing import Any

from cachetools import TTLCache

try:
    from .. import config as app_config
    from ..logger import get_logger
    from ..metrics import SEARCH_CACHE
    from .http_client import post_informacion
    from .circuit_breaker import informacion_cb
    from ._resilience import resilient_call
except ImportError:
    from ventas import config as app_config
    from ventas.logger import get_logger
    from ventas.metrics import SEARCH_CACHE
    from ventas.services.http_client import post_informacion
    from ventas.services.circuit_breaker import informacion_cb
    from ventas.services._resilience import resilient_call

logger = get_logger(__name__)

COD_OPE = "BUSCAR_PRODUCTOS_SERVICIOS_VENTAS_DIRECTAS"
MAX_RESULTADOS = 10

# ---------------------------------------------------------------------------
# Cache de búsquedas
# ---------------------------------------------------------------------------

# Key: (id_empresa, término_normalizado) → resultado completo.
# TTL 15 min: tiempo suficiente para absorber picos de búsquedas repetidas
# entre usuarios distintos de la misma empresa, sin mostrar datos muy viejos.
# maxsize 2000: ~40 términos por empresa para 50 empresas simultáneas.
_busqueda_cache: TTLCache = TTLCache(maxsize=2000, ttl=900)

# Lock por (id_empresa, búsqueda) para anti-thundering herd.
# Mismo patrón que agent_citas. Limpiado en finally.
_busqueda_locks: dict[tuple, asyncio.Lock] = {}


# ---------------------------------------------------------------------------
# Formateo de resultados
# ---------------------------------------------------------------------------

def _clean_description(desc: str | None, max_chars: int = 120) -> str:
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


def _format_item(p: dict[str, Any]) -> list[str]:
    nombre = (p.get("nombre") or "-").strip()
    id_prod = p.get("id")
    precio_str = _format_precio(p.get("precio_unitario"))
    categoria = (p.get("nombre_categoria") or "-").strip()
    descripcion = _clean_description(p.get("descripcion"))
    unidad = (p.get("nombre_unidad") or "unidad").strip().lower()
    lineas = [
        f"### {nombre}",
        f"- ID: {id_prod if id_prod is not None else '-'}",
        f"- Precio: {precio_str} por {unidad}",
        f"- Categoría: {categoria}",
        f"- Descripción: {descripcion}",
        "",
    ]
    return lineas


def format_productos_para_respuesta(productos: list[dict[str, Any]]) -> str:
    """Formatea la lista de productos/servicios para la respuesta de la tool."""
    if not productos:
        return "No se encontraron resultados."
    lineas = []
    for p in productos:
        lineas.extend(_format_item(p))
    return "\n".join(lineas).strip()


# ---------------------------------------------------------------------------
# Llamada a la API (resilient_call + post_informacion)
# ---------------------------------------------------------------------------

async def _do_busqueda_api(
    id_empresa: int,
    busqueda_norm: str,
    cache_key: tuple,
    payload: dict[str, Any],
    log_search_apis: bool,
) -> dict[str, Any]:
    """
    Ejecuta la llamada real a la API con resilient_call. Se llama SOLO desde
    buscar_productos_servicios, dentro de un asyncio.Lock (anti-thundering herd).
    """
    if log_search_apis:
        logger.info("[search_productos_servicios] API: ws_informacion_ia.php - %s", COD_OPE)
        logger.info("  URL: %s", app_config.API_INFORMACION_URL)
        if logger.isEnabledFor(logging.INFO):
            logger.info("  Enviado: %s", json.dumps(payload, ensure_ascii=False))

    try:
        data = await resilient_call(
            lambda: post_informacion(payload),
            cb=informacion_cb,
            circuit_key=id_empresa,
            service_name="BUSQUEDA",
        )

        if log_search_apis and logger.isEnabledFor(logging.INFO):
            logger.info("  Respuesta: %s", json.dumps(data, ensure_ascii=False))

        if not data.get("success"):
            error_msg = data.get("error") or data.get("message") or "Error desconocido"
            logger.warning("[BUSQUEDA] API no success id_empresa=%s: %s", id_empresa, error_msg)
            return {"success": False, "productos": [], "error": error_msg}

        productos = data.get("productos", [])
        resultado = {"success": True, "productos": productos, "error": None}

        # Éxito: cachear resultado
        _busqueda_cache[cache_key] = resultado
        logger.debug(
            "[BUSQUEDA] Cache SET id_empresa=%s busqueda=%r (%s productos)",
            id_empresa, busqueda_norm, len(productos),
        )
        return resultado

    except Exception as e:
        logger.warning(
            "[BUSQUEDA] Error id_empresa=%s busqueda=%r: %s: %s",
            id_empresa, busqueda_norm, type(e).__name__, e,
        )
        return {
            "success": False,
            "productos": [],
            "error": "La búsqueda tardó demasiado. Intenta de nuevo.",
        }


# ---------------------------------------------------------------------------
# Función principal
# ---------------------------------------------------------------------------

async def buscar_productos_servicios(
    id_empresa: int,
    busqueda: str,
    log_search_apis: bool = False,
) -> dict[str, Any]:
    """
    Busca productos y servicios por término (ventas directas).

    Incluye TTLCache 15 min por (id_empresa, búsqueda), anti-thundering herd,
    retry tenacity (TransportError) y circuit breaker compartido (informacion_cb).
    Cantidad de resultados fija en MAX_RESULTADOS (10).

    Args:
        id_empresa: ID de la empresa
        busqueda: Término de búsqueda
        log_search_apis: Si True, registra API, URL, payload y respuesta en info

    Returns:
        Dict con success, productos (lista), error si aplica
    """
    if not busqueda or not str(busqueda).strip():
        return {"success": False, "productos": [], "error": "El término de búsqueda no puede estar vacío"}

    busqueda_norm = str(busqueda).strip()
    cache_key = (id_empresa, busqueda_norm.lower())

    # 1. Cache hit — respuesta inmediata sin tocar la red
    if cache_key in _busqueda_cache:
        SEARCH_CACHE.labels(result="hit").inc()
        logger.debug("[BUSQUEDA] Cache HIT id_empresa=%s busqueda=%r", id_empresa, busqueda_norm)
        return _busqueda_cache[cache_key]

    # 2. Circuit breaker — si la API de esta empresa está fallando, cortar rápido
    if informacion_cb.is_open(id_empresa):
        SEARCH_CACHE.labels(result="circuit_open").inc()
        logger.warning(
            "[BUSQUEDA] Circuit ABIERTO id_empresa=%s — búsqueda rechazada sin llamar API",
            id_empresa,
        )
        return {
            "success": False,
            "productos": [],
            "error": "El servicio de búsqueda no está disponible temporalmente. Intenta en unos minutos.",
        }

    payload = {
        "codOpe": COD_OPE,
        "id_empresa": id_empresa,
        "busqueda": busqueda_norm,
        "limite": MAX_RESULTADOS,
    }

    # 3. Anti-thundering herd: Lock por (id_empresa, búsqueda) + double-check.
    #    Mismo patrón que agent_citas.
    lock = _busqueda_locks.setdefault(cache_key, asyncio.Lock())
    try:
        async with lock:
            # Double-check: otro request puede haber populado el cache
            # mientras esperábamos el lock
            if cache_key in _busqueda_cache:
                SEARCH_CACHE.labels(result="hit").inc()
                logger.debug(
                    "[BUSQUEDA] Cache HIT (post-lock) id_empresa=%s busqueda=%r",
                    id_empresa, busqueda_norm,
                )
                return _busqueda_cache[cache_key]

            SEARCH_CACHE.labels(result="miss").inc()
            return await _do_busqueda_api(
                id_empresa, busqueda_norm, cache_key, payload, log_search_apis
            )
    finally:
        _busqueda_locks.pop(cache_key, None)


__all__ = ["buscar_productos_servicios", "format_productos_para_respuesta"]
