"""
Métricas y observabilidad para el servicio de ventas.
Usa Prometheus (Info) para identificar el agente; /metrics montado en main.
"""

from prometheus_client import Info


agent_info = Info(
    "agent_ventas_info",
    "Información del servicio de ventas",
)


def initialize_agent_info(model: str, version: str = "1.0.0") -> None:
    """Inicializa la información del agente para métricas."""
    agent_info.info({
        "version": version,
        "model": model,
        "agent_type": "ventas",
    })


__all__ = ["initialize_agent_info", "agent_info"]
