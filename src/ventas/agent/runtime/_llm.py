"""
LLM per-tenant y checkpointer LangGraph para el agente de ventas.

get_model(api_key) crea un modelo por tenant (se llama solo en cache miss del agente).
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
    """Crea InMemorySaver con allowlist para VentasStructuredResponse (path msgpack)."""
    return InMemorySaver(
        serde=JsonPlusSerializer(
            allowed_msgpack_modules=[("ventas.agent.content", "VentasStructuredResponse")]
        )
    )


async def init_checkpointer() -> None:
    """
    Inicializa el checkpointer LangGraph.

    Si REDIS_URL está configurado, intenta AsyncRedisSaver con serialización
    JSON (JsonPlusRedisSerializer). Si Redis no está disponible o el paquete
    no está instalado, cae a InMemorySaver como fallback.

    Debe llamarse una sola vez al arrancar la app (FastAPI lifespan).
    """
    global _checkpointer

    if not app_config.REDIS_URL:
        _checkpointer = _make_memory_saver()
        logger.info("[LLM] Checkpointer: InMemorySaver (REDIS_URL vacío)")
        return

    try:
        from langgraph.checkpoint.redis.aio import AsyncRedisSaver
        from langgraph.checkpoint.redis.jsonplus_redis import (
            JsonPlusRedisSerializer,
        )

        ttl_hours = app_config.REDIS_CHECKPOINT_TTL_HOURS
        ttl_config = {"default_ttl": ttl_hours * 60} if ttl_hours > 0 else None

        saver = AsyncRedisSaver(redis_url=app_config.REDIS_URL, ttl=ttl_config)
        saver.serde = JsonPlusRedisSerializer(
            allowed_json_modules=[
                ("ventas", "agent", "content", "VentasStructuredResponse")
            ],
            allowed_msgpack_modules=[
                ("ventas.agent.content", "VentasStructuredResponse")
            ],
        )
        await saver.asetup()
        _checkpointer = saver
        _ttl_label = f"TTL={ttl_hours}h" if ttl_hours > 0 else "sin TTL"
        logger.info(
            "[LLM] Checkpointer: AsyncRedisSaver (%s, %s)",
            app_config.REDIS_URL, _ttl_label,
        )

    except Exception as e:
        logger.warning(
            "[LLM] No se pudo conectar a Redis (%s) — usando InMemorySaver", e
        )
        _checkpointer = _make_memory_saver()


def get_model(api_key: str):
    """
    Crea un modelo LLM con la api_key del tenant.
    Se llama solo en cache miss del agente (~1 vez cada 60 min por empresa).
    init_chat_model es síncrono y barato (microsegundos, sin network call).
    """
    return init_chat_model(
        f"openai:{app_config.OPENAI_MODEL}",
        api_key=api_key,
        temperature=app_config.OPENAI_TEMPERATURE,
        max_tokens=app_config.MAX_TOKENS,
        timeout=app_config.OPENAI_TIMEOUT,
    )


def get_checkpointer():
    """Retorna el checkpointer LangGraph singleton (InMemorySaver o AsyncRedisSaver)."""
    if _checkpointer is None:
        raise RuntimeError(
            "Checkpointer no inicializado. Llamar await init_checkpointer() primero."
        )
    return _checkpointer


async def close_checkpointer() -> None:
    """
    Cierra el checkpointer al apagar la app.
    Si es AsyncRedisSaver, cierra la conexión Redis via __aexit__.
    No-op si es InMemorySaver.
    """
    global _checkpointer

    if _checkpointer is None:
        return

    if hasattr(_checkpointer, "__aexit__"):
        try:
            await _checkpointer.__aexit__(None, None, None)
            logger.info("[LLM] AsyncRedisSaver cerrado correctamente")
        except Exception as e:
            logger.warning("[LLM] Error cerrando Redis checkpointer: %s", e)

    _checkpointer = None


__all__ = ["get_model", "get_checkpointer", "close_checkpointer", "init_checkpointer"]
