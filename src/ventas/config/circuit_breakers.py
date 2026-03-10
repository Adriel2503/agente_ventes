"""
Instancias de CircuitBreaker para las APIs externas de MaravIA.

Cada API tiene su propio CB con partición por key (id_empresa, id_chatbot).
Para agregar una nueva API, crear una instancia aquí y usarla en el servicio.

La clase CircuitBreaker vive en infra/ (infraestructura genérica).
Las instancias viven aquí (configuración de negocio).
"""

from ..infra import CircuitBreaker
from . import CB_THRESHOLD, CB_RESET_TTL

# Keyed by id_empresa.
# Compartido por: categorias, sucursales, metodos_pago, contexto_negocio, busqueda_productos
informacion_cb: CircuitBreaker = CircuitBreaker(
    name="ws_informacion_ia",
    threshold=CB_THRESHOLD,
    reset_ttl=CB_RESET_TTL,
)

# Keyed by id_chatbot.
# Usado por: preguntas_frecuentes
preguntas_cb: CircuitBreaker = CircuitBreaker(
    name="ws_preguntas_frecuentes",
    threshold=CB_THRESHOLD,
    reset_ttl=CB_RESET_TTL,
)

__all__ = ["informacion_cb", "preguntas_cb"]
