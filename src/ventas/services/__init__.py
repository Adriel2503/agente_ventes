"""Servicios del agente de ventas: búsqueda de productos, registro de pedido, y datos para el prompt."""

from .busqueda_productos import buscar_productos_servicios, format_productos_para_respuesta
from .registrar_pedido import registrar_pedido
from .prompt_data.categorias import obtener_categorias, format_categorias_para_prompt
from .prompt_data.contexto_negocio import fetch_contexto_negocio
from .prompt_data.costo_envio import obtener_costos_envio, format_costos_envio_para_prompt
from .prompt_data.metodos_pago import obtener_metodos_pago
from .prompt_data.preguntas_frecuentes import fetch_preguntas_frecuentes, format_preguntas_frecuentes_para_prompt
from .prompt_data.sucursales import obtener_sucursales, format_sucursales_para_prompt

__all__ = [
    "buscar_productos_servicios",
    "format_productos_para_respuesta",
    "registrar_pedido",
    "obtener_categorias",
    "format_categorias_para_prompt",
    "fetch_contexto_negocio",
    "obtener_costos_envio",
    "format_costos_envio_para_prompt",
    "obtener_metodos_pago",
    "fetch_preguntas_frecuentes",
    "format_preguntas_frecuentes_para_prompt",
    "obtener_sucursales",
    "format_sucursales_para_prompt",
]
