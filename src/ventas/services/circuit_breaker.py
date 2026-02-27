"""
Circuit breaker compartido para APIs de MaravIA (agent_ventas).

Singletons:
- informacion_cb  → ws_informacion_ia.php      (keyed by id_empresa)
- preguntas_cb    → ws_preguntas_frecuentes.php (keyed by id_chatbot)

Lógica: después de `threshold` TransportErrors consecutivos para la misma key,
el circuit se abre y el servicio retorna fallback inmediatamente sin llamar a la API.
Se auto-resetea después de `reset_ttl` segundos via TTLCache expiry.
Un éxito antes de abrir resetea el contador.

IMPORTANTE: solo `record_failure()` ante httpx.TransportError (fallos de red/timeout
reales). Las respuestas success=false de la API no abren el circuit.
"""

from typing import Any

from cachetools import TTLCache

try:
    from ..logger import get_logger
    from .. import config as app_config
except ImportError:
    from ventas.logger import get_logger
    from ventas import config as app_config

logger = get_logger(__name__)


class CircuitBreaker:
    """
    Circuit breaker simple con estados CLOSED → OPEN → (TTL) → CLOSED.

    - CLOSED: llamadas pasan normalmente.
    - OPEN: `is_open()` retorna True; el llamador debe retornar fallback sin HTTP.
    - Auto-reset: TTLCache expira el conteo de fallos después de `reset_ttl` segundos.
    """

    def __init__(self, name: str, threshold: int = 3, reset_ttl: int = 300):
        """
        Args:
            name: Nombre descriptivo para logging (ej. "ws_informacion_ia").
            threshold: Cantidad de TransportErrors consecutivos para abrir el circuit.
            reset_ttl: Segundos hasta auto-reset del estado (via TTLCache expiry).
        """
        self.name = name
        self._threshold = threshold
        self._failures: TTLCache = TTLCache(maxsize=500, ttl=reset_ttl)

    def is_open(self, key: Any) -> bool:
        """True si el circuit está abierto para esta key → el llamador debe usar fallback."""
        if self._failures.get(key, 0) >= self._threshold:
            logger.warning("[CB:%s] Circuit ABIERTO para key=%s", self.name, key)
            return True
        return False

    def record_failure(self, key: Any) -> None:
        """
        Registra un fallo de transporte (httpx.TransportError).
        Abre el circuit si el conteo alcanza el threshold.
        """
        current = self._failures.get(key, 0)
        new = current + 1
        self._failures[key] = new
        if new >= self._threshold:
            logger.warning(
                "[CB:%s] Umbral alcanzado key=%s (%s/%s) — circuit ABIERTO por %ss",
                self.name, key, new, self._threshold,
                self._failures.ttl,
            )
        else:
            logger.debug(
                "[CB:%s] Fallo registrado key=%s (%s/%s)",
                self.name, key, new, self._threshold,
            )

    def record_success(self, key: Any) -> None:
        """Registra un éxito. Resetea el contador de fallos (circuit CERRADO)."""
        if key in self._failures:
            self._failures.pop(key, None)
            logger.debug("[CB:%s] Reset por éxito key=%s", self.name, key)

    def any_open(self) -> bool:
        """True si al menos un circuit está abierto. Usado por /health para reportar degradación."""
        return any(count >= self._threshold for count in self._failures.values())


# ---------------------------------------------------------------------------
# Singletons compartidos entre servicios
# ---------------------------------------------------------------------------

# Keyed by id_empresa.
# Compartido por: categorias, sucursales, metodos_pago, contexto_negocio, busqueda_productos
informacion_cb: CircuitBreaker = CircuitBreaker(
    name="ws_informacion_ia",
    threshold=app_config.CB_THRESHOLD,
    reset_ttl=app_config.CB_RESET_TTL,
)

# Keyed by id_chatbot.
# Usado por: preguntas_frecuentes
preguntas_cb: CircuitBreaker = CircuitBreaker(
    name="ws_preguntas_frecuentes",
    threshold=app_config.CB_THRESHOLD,
    reset_ttl=app_config.CB_RESET_TTL,
)

__all__ = ["CircuitBreaker", "informacion_cb", "preguntas_cb"]
