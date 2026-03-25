"""
Singleton LLM y checkpointer LangGraph para el agente de ventas.

Inicialización lazy del modelo (get_model) igual que get_client en http_client.py.
El checkpointer se crea en init_checkpointer() (async, llamado desde lifespan).
"""

from __future__ import annotations

from typing import Any

from langchain.chat_models import init_chat_model
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from ... import config as app_config
from ...logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Checkpointer LangGraph (singleton, inicializado en init_checkpointer)
# ---------------------------------------------------------------------------

_checkpointer: Any = None

def _make_memory_saver() -> InMemorySaver:
    """Crea InMemorySaver con allowlist para VentasStructuredResponse."""
    return InMemorySaver(
        serde=JsonPlusSerializer(
            allowed_msgpack_modules=[("ventas.agent.content", "VentasStructuredResponse")]
        )
    )


async def init_checkpointer() -> None:
    """Inicializa el checkpointer. Llamar en lifespan startup."""
    global _checkpointer
    _checkpointer = _make_memory_saver()
    logger.info("[LLM] Checkpointer: InMemorySaver")


def get_checkpointer():
    """Retorna el checkpointer singleton. Lanza si no está inicializado."""
    if _checkpointer is None:
        raise RuntimeError(
            "Checkpointer no inicializado. Llamar await init_checkpointer() primero."
        )
    return _checkpointer


async def close_checkpointer() -> None:
    """Cierra el checkpointer al apagar la app."""
    global _checkpointer
    if _checkpointer is None:
        return
    if hasattr(_checkpointer, "__aexit__"):
        try:
            await _checkpointer.__aexit__(None, None, None)
            logger.info("[LLM] Checkpointer cerrado correctamente")
        except Exception as e:
            logger.warning("[LLM] Error cerrando checkpointer: %s", e)
    _checkpointer = None


def get_model(api_key: str):
    """
    Crea un modelo LLM para la api_key dada.
    No es singleton: cada empresa puede tener su propia key.
    El cache del agente en _cache.py evita recrear el modelo en cada mensaje.
    """
    logger.info("[LLM] Creando modelo LLM: %s", app_config.OPENAI_MODEL)
    return init_chat_model(
        f"openai:{app_config.OPENAI_MODEL}",
        api_key=api_key,
        temperature=app_config.OPENAI_TEMPERATURE,
        max_tokens=app_config.MAX_TOKENS,
        timeout=app_config.OPENAI_TIMEOUT,
    )


__all__ = ["get_model", "get_checkpointer", "close_checkpointer", "init_checkpointer"]
