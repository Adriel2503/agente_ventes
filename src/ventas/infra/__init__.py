"""Infraestructura transversal: HTTP client, circuit breaker y resiliencia."""

from .circuit_breaker import CircuitBreaker
from .http_client import get_client, close_http_client, post_with_logging, post_with_retry
from ._resilience import resilient_call

__all__ = [
    "get_client",
    "close_http_client",
    "post_with_logging",
    "post_with_retry",
    "CircuitBreaker",
    "resilient_call",
]
