"""
Búsqueda de productos y servicios desde ws_informacion_ia.php.
Usa codOpe: BUSCAR_PRODUCTOS_SERVICIOS_VENTAS_DIRECTAS

Resiliencia:
  - TTLCache 15 min por (id_empresa, búsqueda): absorbe búsquedas repetidas
    del mismo término entre usuarios de la misma empresa.
  - Retry: 1 reintento con backoff 0.5s para fallos de red transitorios.
  - Circuit breaker: 3 fallos → corte de 3 min por empresa (auto-reset).
"""

import asyncio
import json
import logging
import re
from typing import Any

from cachetools import TTLCache

try:
    from .. import config as app_config
    from ..metrics import SEARCH_CACHE
    from ..services.api_informacion import post_informacion
except ImportError:
    from ventas import config as app_config
    from ventas.metrics import SEARCH_CACHE
    from ventas.services.api_informacion import post_informacion

logger = logging.getLogger(__name__)

COD_OPE = "BUSCAR_PRODUCTOS_SERVICIOS_VENTAS_DIRECTAS"

# ---------------------------------------------------------------------------
# Cache de búsquedas
# ---------------------------------------------------------------------------

# Key: (id_empresa, término_normalizado) → resultado completo.
# TTL 15 min: tiempo suficiente para absorber picos de búsquedas repetidas
# entre usuarios distintos de la misma empresa, sin mostrar datos muy viejos.
# maxsize 2000: ~40 términos por empresa para 50 empresas simultáneas.
_busqueda_cache: TTLCache = TTLCache(maxsize=2000, ttl=900)

# ---------------------------------------------------------------------------
# Circuit breaker por empresa
# ---------------------------------------------------------------------------

# TTLCache como circuit breaker: el auto-reset ocurre a los 3 min (TTL del dict).
# Si la API de una empresa falla 3 veces seguidas, se corta el circuito y
# las búsquedas devuelven error inmediato hasta que el TTL resetee el contador.
_busqueda_failures: TTLCache = TTLCache(maxsize=500, ttl=180)  # 3 min auto-reset
_FAILURE_THRESHOLD = 3


def _is_circuit_open(id_empresa: int) -> bool:
    """True si el circuit breaker está abierto para esta empresa."""
    return _busqueda_failures.get(id_empresa, 0) >= _FAILURE_THRESHOLD


def _record_failure(id_empresa: int) -> None:
    """Incrementa el contador de fallos del circuit breaker."""
    _busqueda_failures[id_empresa] = _busqueda_failures.get(id_empresa, 0) + 1


def _reset_failures(id_empresa: int) -> None:
    """Resetea el circuit breaker tras una llamada exitosa."""
    _busqueda_failures.pop(id_empresa, None)


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


def format_productos_para_respuesta(productos: list[dict[str, Any]]) -> str:
    """Formatea la lista de productos/servicios para la respuesta de la tool."""
    if not productos:
        return "No se encontraron resultados."
    lineas = []
    for p in productos:
        lineas.extend(_format_item(p))
    return "\n".join(lineas).strip()


# ---------------------------------------------------------------------------
# Función principal
# ---------------------------------------------------------------------------

async def buscar_productos_servicios(
    id_empresa: int,
    busqueda: str,
    limite: int = 10,
    log_search_apis: bool = False,
) -> dict[str, Any]:
    """
    Busca productos y servicios por término (ventas directas).

    Incluye TTLCache 15 min por (id_empresa, búsqueda), 1 reintento con
    backoff 0.5s y circuit breaker (3 fallos → corte 3 min por empresa).

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

    busqueda_norm = str(busqueda).strip()
    cache_key = (id_empresa, busqueda_norm.lower())

    # 1. Cache hit — respuesta inmediata sin tocar la red
    if cache_key in _busqueda_cache:
        SEARCH_CACHE.labels(result="hit").inc()
        logger.debug("[BUSQUEDA] Cache HIT id_empresa=%s busqueda=%r", id_empresa, busqueda_norm)
        return _busqueda_cache[cache_key]

    # 2. Circuit breaker — si la API de esta empresa está fallando, cortar rápido
    if _is_circuit_open(id_empresa):
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

    # 3. Cache miss — se va a la red
    SEARCH_CACHE.labels(result="miss").inc()

    payload = {
        "codOpe": COD_OPE,
        "id_empresa": id_empresa,
        "busqueda": busqueda_norm,
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

    # Retry loop: 1 reintento con backoff 0.5s
    # Solo se reintenta en fallos de red/timeout (excepciones), no en success:false
    # de la API (eso es respuesta válida del negocio, no error transitorio).
    max_retries = 2
    for attempt in range(max_retries):
        try:
            data = await post_informacion(payload)

            if log_search_apis:
                logger.info("  Respuesta: %s", json.dumps(data, ensure_ascii=False))

            if not data.get("success"):
                error_msg = data.get("error") or data.get("message") or "Error desconocido"
                logger.warning("[BUSQUEDA] API no success id_empresa=%s: %s", id_empresa, error_msg)
                return {"success": False, "productos": [], "error": error_msg}

            productos = data.get("productos", [])
            resultado = {"success": True, "productos": productos, "error": None}

            # Éxito: cachear resultado y resetear circuit breaker
            _busqueda_cache[cache_key] = resultado
            _reset_failures(id_empresa)
            logger.debug(
                "[BUSQUEDA] Cache SET id_empresa=%s busqueda=%r (%s productos)",
                id_empresa, busqueda_norm, len(productos),
            )
            return resultado

        except Exception as e:
            logger.warning(
                "[BUSQUEDA] Error intento %d/%d id_empresa=%s busqueda=%r: %s: %s",
                attempt + 1, max_retries, id_empresa, busqueda_norm, type(e).__name__, e,
            )
            if attempt < max_retries - 1:
                await asyncio.sleep(0.5)

    # Todos los intentos fallaron → registrar fallo en circuit breaker
    _record_failure(id_empresa)
    failures = _busqueda_failures.get(id_empresa, 0)
    if failures >= _FAILURE_THRESHOLD:
        logger.warning(
            "[BUSQUEDA] Circuit breaker ABIERTO id_empresa=%s (fallos acumulados=%s)",
            id_empresa, failures,
        )

    return {
        "success": False,
        "productos": [],
        "error": "La búsqueda tardó demasiado o tuvo un error. Intenta de nuevo.",
    }


__all__ = ["buscar_productos_servicios", "format_productos_para_respuesta"]
