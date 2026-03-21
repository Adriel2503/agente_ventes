"""
Modelo de contexto runtime y función de preparación.
"""

from dataclasses import dataclass
from typing import Any


@dataclass
class AgentContext:
    """Contexto runtime para el agente (inyectado en las tools)."""
    id_empresa: int
    session_id: int = 0


def _prepare_agent_context(config: dict[str, Any], session_id: int) -> AgentContext:
    return AgentContext(
        id_empresa=config["id_empresa"],
        session_id=session_id,
    )
