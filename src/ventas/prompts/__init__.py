"""
Prompts del agente de ventas. Builder del system prompt.
"""

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict

from jinja2 import Environment, FileSystemLoader, select_autoescape

try:
    from ..services.categorias import obtener_categorias
    from ..services.contexto_negocio import fetch_contexto_negocio
    from ..services.metodos_pago import obtener_metodos_pago
    from ..services.preguntas_frecuentes import fetch_preguntas_frecuentes
    from ..services.sucursales import obtener_sucursales
except ImportError:
    from ventas.services.categorias import obtener_categorias
    from ventas.services.contexto_negocio import fetch_contexto_negocio
    from ventas.services.metodos_pago import obtener_metodos_pago
    from ventas.services.preguntas_frecuentes import fetch_preguntas_frecuentes
    from ventas.services.sucursales import obtener_sucursales

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).resolve().parent

_DEFAULTS: Dict[str, Any] = {
    "personalidad": "amable, profesional y cercano",
    "nombre_asistente": "asistente comercial",
    "nombre_negocio": "la empresa",
    "propuesta_valor": "Ofrecemos productos de calidad con entrega rápida.",
    "medios_pago": "",
}


def _apply_defaults(config: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(_DEFAULTS)
    for k, v in config.items():
        if v is not None and v != "" and v != []:
            out[k] = v
    return out


async def build_ventas_system_prompt(config: Dict[str, Any]) -> str:
    """
    Construye el system prompt del agente de ventas.

    Args:
        config: Diccionario con id_empresa (para categorías), nombre_negocio,
                personalidad, medios_pago (texto opcional), etc.

    Returns:
        System prompt formateado.
    """
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(disabled_extensions=()),
    )
    template = env.get_template("ventas_system.j2")
    variables = _apply_defaults(config)

    # Compatibilidad con mismo payload que citas: nombre_bot → nombre_negocio si no viene nombre_negocio
    nombre_bot = config.get("nombre_bot")
    if nombre_bot and (not variables.get("nombre_negocio") or variables.get("nombre_negocio") == "la empresa"):
        variables["nombre_negocio"] = nombre_bot

    id_empresa = config.get("id_empresa")
    id_chatbot = config.get("id_chatbot")

    if id_empresa is not None:
        id_emp = int(id_empresa)
        r_cat, r_suc, r_med, r_ctx, r_faq = await asyncio.gather(
            obtener_categorias(id_emp),
            obtener_sucursales(id_emp),
            obtener_metodos_pago(id_emp),
            fetch_contexto_negocio(id_emp),
            fetch_preguntas_frecuentes(id_chatbot),
            return_exceptions=True,
        )
        # Degradación elegante: si una tarea lanzó, usar el mismo default que ese servicio
        _default_categorias = (
            "No hay información de productos y servicios cargada. "
            "Usa la herramienta search_productos_servicios cuando pregunten por algo concreto."
        )
        informacion_productos = _default_categorias if isinstance(r_cat, BaseException) else r_cat
        informacion_sucursales = "" if isinstance(r_suc, BaseException) else r_suc
        medios_pago_texto = "" if isinstance(r_med, BaseException) else r_med
        contexto_negocio = r_ctx if not isinstance(r_ctx, BaseException) else None
        preguntas_frecuentes_str = r_faq if not isinstance(r_faq, BaseException) else ""
        if isinstance(r_cat, BaseException):
            logger.warning("[PROMPT] categorías falló: %s - %s", type(r_cat).__name__, r_cat)
        if isinstance(r_suc, BaseException):
            logger.warning("[PROMPT] sucursales falló: %s - %s", type(r_suc).__name__, r_suc)
        if isinstance(r_med, BaseException):
            logger.warning("[PROMPT] medios de pago falló: %s - %s", type(r_med).__name__, r_med)
        if isinstance(r_ctx, BaseException):
            logger.warning("[PROMPT] contexto_negocio falló: %s - %s", type(r_ctx).__name__, r_ctx)
        if isinstance(r_faq, BaseException):
            logger.warning("[PROMPT] preguntas_frecuentes falló: %s - %s", type(r_faq).__name__, r_faq)
        variables["informacion_productos_servicios"] = informacion_productos
        variables["informacion_sucursales"] = informacion_sucursales
        variables["medios_pago"] = medios_pago_texto or variables.get("medios_pago", "")
        variables["contexto_negocio"] = contexto_negocio
        variables["preguntas_frecuentes"] = preguntas_frecuentes_str or ""
    else:
        variables["informacion_productos_servicios"] = (
            "No hay información de productos y servicios cargada. "
            "Usa la herramienta search_productos_servicios cuando pregunten por algo concreto."
        )
        variables["informacion_sucursales"] = ""
        variables["contexto_negocio"] = None
        # FAQs se pueden cargar solo con id_chatbot (mismo payload que citas)
        preguntas_frecuentes_str = await fetch_preguntas_frecuentes(id_chatbot)
        variables["preguntas_frecuentes"] = preguntas_frecuentes_str or ""
        # medios_pago queda con el default de _apply_defaults (vacío) o el que venga en config

    return template.render(**variables)


__all__ = ["build_ventas_system_prompt"]
