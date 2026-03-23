"""
Métricas y observabilidad para el servicio de ventas.
Expone contadores, histogramas e info estática para Prometheus.
/metrics montado en main.py.
"""

import time
from contextlib import contextmanager

from prometheus_client import Counter, Gauge, Histogram, Info

# ---------------------------------------------------------------------------
# Info estática (versión, modelo)
# ---------------------------------------------------------------------------

agent_info = Info(
    "ventas_info",
    "Información del servicio de ventas",
)


def initialize_agent_info(model: str, version: str = "1.0.0") -> None:
    """Inicializa la información estática del agente para métricas."""
    agent_info.info({
        "version": version,
        "model": model,
        "agent_type": "ventas",
    })


# ---------------------------------------------------------------------------
# Capa HTTP (/api/chat)
# ---------------------------------------------------------------------------

HTTP_REQUESTS = Counter(
    "ventas_http_requests_total",
    "Total de requests al endpoint /api/chat por resultado",
    ["status"],  # success | timeout | error
)

HTTP_DURATION = Histogram(
    "ventas_http_duration_seconds",
    "Latencia total del endpoint /api/chat (incluye LLM y tools)",
    buckets=[0.25, 0.5, 1, 2.5, 5, 10, 20, 30, 60, 90, 120],
)

# ---------------------------------------------------------------------------
# Capa LLM (agent.ainvoke — turno completo incluye tool calls internos)
# ---------------------------------------------------------------------------

LLM_REQUESTS = Counter(
    "ventas_llm_requests_total",
    "Total de invocaciones al agente LLM por resultado",
    ["status"],  # success | error
)

LLM_DURATION = Histogram(
    "ventas_llm_duration_seconds",
    "Latencia de agent.ainvoke (LLM + tool calls dentro de LangGraph)",
    buckets=[0.5, 1, 2, 5, 10, 20, 30, 60, 90],
)

CHAT_RESPONSE_DURATION = Histogram(
    "ventas_chat_response_duration_seconds",
    "Latencia total del procesamiento de mensaje (lock + ainvoke + resultado)",
    ["status"],  # success | error
    buckets=[0.1, 0.5, 1, 2, 5, 10, 20, 30, 60, 90],
)

# ---------------------------------------------------------------------------
# Cache del agente (por empresa)
# ---------------------------------------------------------------------------

AGENT_CACHE = Counter(
    "ventas_agent_cache_total",
    "Hits y misses del cache de agente por empresa",
    ["result"],  # hit | miss
)

# ---------------------------------------------------------------------------
# Tool calls
# ---------------------------------------------------------------------------

TOOL_CALLS = Counter(
    "ventas_tool_calls_total",
    "Invocaciones de tools del agente por herramienta y resultado",
    ["tool", "status"],  # tool: nombre de la tool; status: ok | error
)

# ---------------------------------------------------------------------------
# Cache de búsqueda de productos
# ---------------------------------------------------------------------------

SEARCH_CACHE = Counter(
    "ventas_search_cache_total",
    "Resultados del cache de búsqueda de productos",
    ["result"],  # hit | miss | circuit_open
)

# ---------------------------------------------------------------------------
# Gauges (estado actual)
# ---------------------------------------------------------------------------

cache_entries = Gauge(
    "ventas_cache_entries",
    "Número de entradas en cache",
    ["cache_type"],
)


def update_cache_stats(cache_type: str, count: int) -> None:
    """Actualiza estadísticas de cache."""
    cache_entries.labels(cache_type=cache_type).set(count)


# ---------------------------------------------------------------------------
# Por empresa (como agent_citas)
# ---------------------------------------------------------------------------

CHAT_REQUESTS = Counter(
    "ventas_chat_requests_total",
    "Total de requests de chat por empresa",
    ["empresa_id"],
)

CHAT_ERRORS = Counter(
    "ventas_chat_errors_total",
    "Total de errores de chat por tipo",
    ["error_type"],
)


# ---------------------------------------------------------------------------
# Context managers (como agent_citas)
# ---------------------------------------------------------------------------

@contextmanager
def track_chat_response():
    """Context manager para medir la latencia total del procesamiento de mensaje."""
    status = "success"
    start = time.perf_counter()
    try:
        yield
    except Exception:
        status = "error"
        raise
    finally:
        CHAT_RESPONSE_DURATION.labels(status=status).observe(time.perf_counter() - start)


@contextmanager
def track_llm_call():
    """Context manager para medir la duración del ainvoke (LLM puro + tool calls)."""
    status = "success"
    start = time.perf_counter()
    try:
        yield
    except Exception:
        status = "error"
        raise
    finally:
        LLM_REQUESTS.labels(status=status).inc()
        LLM_DURATION.observe(time.perf_counter() - start)


def record_chat_error(error_type: str) -> None:
    """Registra un error de chat por tipo."""
    CHAT_ERRORS.labels(error_type=error_type).inc()


__all__ = [
    "initialize_agent_info",
    "agent_info",
    "HTTP_REQUESTS",
    "HTTP_DURATION",
    "LLM_REQUESTS",
    "LLM_DURATION",
    "CHAT_RESPONSE_DURATION",
    "AGENT_CACHE",
    "TOOL_CALLS",
    "SEARCH_CACHE",
    "CHAT_REQUESTS",
    "CHAT_ERRORS",
    "cache_entries",
    "update_cache_stats",
    "track_chat_response",
    "track_llm_call",
    "record_chat_error",
]
