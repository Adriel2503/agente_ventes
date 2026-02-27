"""
Helper de resiliencia compartido: circuit breaker.

El retry ya lo maneja tenacity en post_with_retry (http_client.py).
Este módulo solo se ocupa de verificar/actualizar el estado del CB.

Uso:
    from .circuit_breaker import informacion_cb

    data = await resilient_call(
        lambda: post_informacion(payload),
        cb=informacion_cb,
        circuit_key=id_empresa,
        service_name="MI_SERVICIO",
    )
    # Lanza RuntimeError si circuit abierto, o la excepción original si la llamada falla.
"""

from typing import Any, Awaitable, Callable

import httpx

from .circuit_breaker import CircuitBreaker

try:
    from ..logger import get_logger
except ImportError:
    from ventas.logger import get_logger

logger = get_logger(__name__)


async def resilient_call(
    coro_factory: Callable[[], Awaitable[Any]],
    cb: CircuitBreaker,
    circuit_key: Any,
    service_name: str,
) -> Any:
    """
    Ejecuta coro_factory() con circuit breaker.

    - Circuit breaker abierto → RuntimeError inmediato, sin tocar la red.
    - Éxito → resetea el contador de fallos del CB.
    - httpx.TransportError → incrementa el contador del CB y re-lanza.
    - Otros errores (HTTPStatusError, etc.) → re-lanza sin afectar el CB.

    El retry ante fallos de red transitorios lo maneja tenacity dentro de
    post_with_retry (http_client.py); este wrapper solo gestiona el CB.

    Args:
        coro_factory:  Callable sin argumentos que retorna una coroutine.
        cb:            CircuitBreaker compartido para este servicio.
        circuit_key:   Clave de partición del circuit breaker (ej: id_empresa).
        service_name:  Nombre para logs (ej: "CATEGORIAS").

    Raises:
        RuntimeError: si el circuit breaker está abierto.
        httpx.TransportError: si la llamada falla por red (CB actualizado).
        Exception: cualquier otro error de la coroutine (CB no afectado).
    """
    if cb.is_open(circuit_key):
        logger.warning(
            "[%s] Circuit ABIERTO key=%s — llamada rechazada sin tocar la red",
            service_name, circuit_key,
        )
        raise RuntimeError(
            f"[{service_name}] Circuit breaker abierto para key={circuit_key}"
        )

    try:
        result = await coro_factory()
        cb.record_success(circuit_key)
        return result
    except httpx.TransportError as exc:
        logger.warning(
            "[%s] TransportError key=%s: %s",
            service_name, circuit_key, exc,
        )
        cb.record_failure(circuit_key)
        raise
    except Exception:
        # HTTPStatusError, errores de negocio, etc. no afectan el circuit breaker.
        raise


__all__ = ["resilient_call"]
