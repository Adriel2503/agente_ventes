"""
Prompts del agente de ventas. Builder del system prompt.
"""

import logging
from pathlib import Path
from typing import Any, Dict

from jinja2 import Environment, FileSystemLoader, select_autoescape

try:
    from ..services.categorias import obtener_categorias
except ImportError:
    from ventas.services.categorias import obtener_categorias

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


def build_ventas_system_prompt(config: Dict[str, Any]) -> str:
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

    # Inyectar información de productos y servicios (categorías API) para "¿qué tienen?"
    id_empresa = config.get("id_empresa")
    if id_empresa is not None:
        variables["informacion_productos_servicios"] = obtener_categorias(int(id_empresa))
    else:
        variables["informacion_productos_servicios"] = (
            "No hay información de productos y servicios cargada. "
            "Usa la herramienta search_productos_servicios cuando pregunten por algo concreto."
        )

    return template.render(**variables)


__all__ = ["build_ventas_system_prompt"]
