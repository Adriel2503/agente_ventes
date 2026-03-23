"""
Modelo de contexto runtime y función de preparación.
"""

from dataclasses import dataclass


@dataclass
class AgentContext:
    """Contexto runtime para el agente (inyectado en las tools)."""
    id_empresa: int
    session_id: int = 0


def _prepare_agent_context(id_empresa: int, session_id: int) -> AgentContext:
    return AgentContext(
        id_empresa=id_empresa,
        session_id=session_id,
    )
