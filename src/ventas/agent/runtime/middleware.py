"""
Middleware LangChain para limitar la ventana de mensajes enviados al LLM.

Recorta el historial a MAX_MESSAGES_HISTORY mensajes antes de cada llamada al LLM,
preservando el historial completo en el checkpointer.
"""

from langchain.agents.middleware import wrap_model_call, ModelRequest, ModelResponse
from langchain_core.messages import trim_messages

from ... import config as app_config


@wrap_model_call
async def message_window(request: ModelRequest, handler) -> ModelResponse:
    """Limita los mensajes enviados al LLM a MAX_MESSAGES_HISTORY.
    No modifica el checkpointer — solo recorta lo que ve el LLM en cada llamada.
    """
    if not request.messages:
        return await handler(request)
    trimmed = trim_messages(
        list(request.messages),
        max_tokens=app_config.MAX_MESSAGES_HISTORY,
        strategy="last",
        token_counter=len,      # cuenta mensajes, no tokens reales
        allow_partial=False,    # nunca corta un par AI↔Tool
        include_system=True,    # preserva el system prompt
        start_on="human",       # el recorte siempre empieza en msg del usuario
    )
    return await handler(request.override(messages=trimmed))


__all__ = ["message_window"]
