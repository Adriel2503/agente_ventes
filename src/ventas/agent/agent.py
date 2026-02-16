"""
Lógica del agente especializado en venta directa usando LangChain 1.2+ API moderna.
"""

from typing import Any, Dict
from dataclasses import dataclass

from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langgraph.checkpoint.memory import InMemorySaver

try:
    from .. import config as app_config
    from ..tool.tools import AGENT_TOOLS
    from ..logger import get_logger
    from ..prompts import build_ventas_system_prompt
except ImportError:
    from ventas import config as app_config
    from ventas.tool.tools import AGENT_TOOLS
    from ventas.logger import get_logger
    from ventas.prompts import build_ventas_system_prompt

logger = get_logger(__name__)

_checkpointer = InMemorySaver()


@dataclass
class AgentContext:
    """Contexto runtime para el agente (inyectado en las tools)."""
    id_empresa: int
    session_id: int = 0


def _validate_context(context: Dict[str, Any]) -> None:
    config_data = context.get("config", {})
    required_keys = ["id_empresa"]
    missing = [k for k in required_keys if k not in config_data or config_data[k] is None]
    if missing:
        raise ValueError(f"Context missing required keys in config: {missing}")
    logger.debug("[AGENT] Context validated: id_empresa=%s", config_data.get("id_empresa"))


def _get_agent(config: Dict[str, Any]):
    """Crea el agente LangChain con tools y checkpointer."""
    logger.debug("[AGENT] Creando agente con LangChain 1.2+ API")

    model = init_chat_model(
        f"openai:{app_config.OPENAI_MODEL}",
        api_key=app_config.OPENAI_API_KEY,
        temperature=app_config.OPENAI_TEMPERATURE,
        max_tokens=app_config.MAX_TOKENS,
        timeout=app_config.OPENAI_TIMEOUT,
    )

    system_prompt = build_ventas_system_prompt(config=config)

    agent = create_agent(
        model=model,
        tools=AGENT_TOOLS,
        system_prompt=system_prompt,
        checkpointer=_checkpointer,
    )

    logger.debug("[AGENT] Agente creado - Tools: %s", len(AGENT_TOOLS))
    return agent


def _prepare_agent_context(context: Dict[str, Any], session_id: int) -> AgentContext:
    config_data = context.get("config", {})
    return AgentContext(
        id_empresa=config_data["id_empresa"],
        session_id=session_id,
    )


async def process_venta_message(
    message: str,
    session_id: int,
    context: Dict[str, Any],
) -> str:
    """
    Procesa un mensaje del cliente sobre ventas usando el agente LangChain.

    Args:
        message: Mensaje del cliente
        session_id: ID de sesión (unificado con orquestador)
        context: Contexto con config (id_empresa, personalidad, nombre_negocio, etc.)

    Returns:
        Respuesta del agente de ventas
    """
    if not message or not message.strip():
        return "No recibí tu mensaje. ¿Podrías repetirlo?"

    if session_id is None or session_id < 0:
        raise ValueError("session_id es requerido (entero no negativo)")

    try:
        _validate_context(context)
    except ValueError as e:
        logger.error("[AGENT] Error de contexto: %s", e)
        return f"Error de configuración: {str(e)}"

    config_data = context.get("config", {})
    if not config_data.get("personalidad"):
        config_data["personalidad"] = "amable, profesional y cercano"

    try:
        agent = _get_agent(config_data)
    except Exception as e:
        logger.error("[AGENT] Error creando agente: %s", e, exc_info=True)
        return "Disculpa, tuve un problema de configuración. ¿Podrías intentar nuevamente?"

    agent_context = _prepare_agent_context(context, session_id)

    config = {"configurable": {"thread_id": str(session_id)}}

    try:
        logger.debug("[AGENT] Invocando agente - Session: %s", session_id)

        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": message}]},
            config=config,
            context=agent_context,
        )

        messages = result.get("messages", [])
        if messages:
            last_message = messages[-1]
            response_text = (
                last_message.content
                if hasattr(last_message, "content")
                else str(last_message)
            )
        else:
            response_text = "Lo siento, no pude procesar tu solicitud."

        logger.debug("[AGENT] Respuesta generada: %s...", response_text[:200])

    except Exception as e:
        logger.error("[AGENT] Error al ejecutar agente: %s", e, exc_info=True)
        return "Disculpa, tuve un problema al procesar tu mensaje. ¿Podrías intentar nuevamente?"

    return response_text
