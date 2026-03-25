"""
Lógica del agente especializado en venta directa usando LangChain 1.2+ API moderna.
"""

from __future__ import annotations

import asyncio
import hashlib

import openai
from langchain.agents import create_agent

from .. import config as app_config
from ..schemas import VentasConfig
from ..tool.tools import AGENT_TOOLS
from ..logger import get_logger
from ..metrics import AGENT_CACHE, track_chat_response, track_llm_call, CHAT_REQUESTS, record_chat_error, update_cache_stats, record_token_usage
from .prompts import build_ventas_system_prompt
from .content import VentasStructuredResponse, _build_content
from .context import _prepare_agent_context
from .runtime import (
    get_model, get_checkpointer,
    get_cached_agent, cache_agent, agent_cache_size, agent_cache_ttl,
    acquire_agent_lock, release_agent_lock, acquire_session_lock,
    message_window,
)

logger = get_logger(__name__)

# Mapeo de errores OpenAI: tipo → (log_level, metric_key, log_tag, mensaje_usuario)
_OPENAI_ERRORS: dict[type, tuple[str, str, str, str]] = {
    openai.AuthenticationError: ("critical", "openai_auth_error", "OpenAI-401", "No puedo procesar tu mensaje, la clave de acceso al servicio no es válida."),
    openai.RateLimitError:      ("warning",  "openai_rate_limit", "OpenAI-429", "Estoy recibiendo demasiadas solicitudes en este momento, por favor intenta en unos segundos."),
    openai.InternalServerError: ("error",    "openai_server_error", "OpenAI-5xx", "El servicio de inteligencia artificial está presentando problemas, por favor intenta nuevamente."),
    openai.APIConnectionError:  ("error",    "openai_connection_error", "OpenAI-conn", "No pude conectarme al servicio de inteligencia artificial, por favor intenta nuevamente."),
    openai.BadRequestError:     ("warning",  "openai_bad_request", "OpenAI-400", "Tu mensaje no pudo ser procesado por el servicio, ¿puedes reformularlo?"),
}

# Backpressure: limita invocaciones concurrentes al agente (OpenAI + tools)
_semaphore = asyncio.Semaphore(app_config.MAX_CONCURRENT_AGENT)


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

async def _build_agent_for_empresa(id_empresa: int, api_key: str, config: VentasConfig):
    """
    Construye un nuevo agente para la empresa. Se llama SOLO en cache miss.
    El agente resultante es compartido por todos los usuarios de esa empresa;
    el aislamiento de sesión lo provee el checkpointer vía thread_id.
    """
    logger.info("[AGENT] Construyendo agente para id_empresa=%s", id_empresa)
    model = get_model(api_key)
    system_prompt = await build_ventas_system_prompt(id_empresa=id_empresa, config=config)
    agent = create_agent(
        model=model,
        tools=AGENT_TOOLS,
        system_prompt=system_prompt,
        checkpointer=get_checkpointer(),
        response_format=VentasStructuredResponse,
        middleware=[message_window],
    )
    logger.info(
        "[AGENT] Agente listo para id_empresa=%s (tools=%s, TTL=%s min)",
        id_empresa, len(AGENT_TOOLS), app_config.AGENT_CACHE_TTL_MINUTES,
    )
    return agent


async def _get_agent(id_empresa: int, api_key: str, config: VentasConfig):
    """
    Retorna el agente para esta empresa.

    - Fast path (cache hit): O(1), sin I/O.
    - Slow path (cache miss): Lock por cache_key + double-check post-lock.
      N requests concurrentes serializan; solo el primero construye.
    """
    _key_hash = hashlib.sha256(api_key.encode()).hexdigest()[:12]
    cache_key: tuple = (id_empresa, _key_hash)

    # Fast path — sin lock
    cached = get_cached_agent(cache_key)
    if cached is not None:
        AGENT_CACHE.labels(result="hit").inc()
        update_cache_stats("agent", agent_cache_size())
        logger.debug("[AGENT] Cache HIT id_empresa=%s", id_empresa)
        return cached

    lock = acquire_agent_lock(cache_key)
    try:
        async with lock:
            # Double-check: otro request puede haber construido el agente
            # mientras esperábamos el lock
            cached = get_cached_agent(cache_key)
            if cached is not None:
                AGENT_CACHE.labels(result="hit").inc()
                update_cache_stats("agent", agent_cache_size())
                logger.debug("[AGENT] Cache HIT (post-lock) id_empresa=%s", id_empresa)
                return cached

            AGENT_CACHE.labels(result="miss").inc()
            logger.info("[AGENT] Cache MISS id_empresa=%s — iniciando build", id_empresa)
            agent = await _build_agent_for_empresa(id_empresa, api_key, config)
            cache_agent(cache_key, agent)
            update_cache_stats("agent", agent_cache_size())
            return agent
    finally:
        release_agent_lock(cache_key)


# ---------------------------------------------------------------------------
# Punto de entrada público
# ---------------------------------------------------------------------------

async def process_venta_message(
    message: str,
    session_id: int,
    id_empresa: int,
    api_key: str,
    config: VentasConfig | None,
) -> tuple[str, str | None]:
    """
    Procesa un mensaje del cliente sobre ventas usando el agente LangChain.

    El agente se obtiene del cache por id_empresa (TTL=AGENT_CACHE_TTL_MINUTES min).
    El historial de conversación se aísla por session_id via thread_id
    en el checkpointer (InMemorySaver). Los requests concurrentes del mismo
    session_id se serializan vía session locks para evitar race conditions.

    Args:
        message: Mensaje del cliente
        session_id: ID estable del usuario (viene del gateway)
        id_empresa: ID de la empresa
        config: Configuración opcional del bot (VentasConfig)

    Returns:
        Tupla (reply, url). url es None cuando no hay medio que adjuntar.
    """
    if not message or not message.strip():
        return ("No recibí tu mensaje. ¿Podrías repetirlo?", None)

    # Comandos del sistema (interceptados antes del lock y del agente)
    _cmd = message.strip().lower()
    if _cmd == "/clear":
        if session_id is not None and session_id >= 0:
            await get_checkpointer().adelete_thread(str(session_id))
        logger.info("[CMD] /clear - Session: %s | Historial borrado", session_id)
        return ("Historial limpiado. ¿En qué puedo ayudarte?", None)

    if _cmd == "/restart":
        logger.warning("[CMD] /restart - Session: %s | Comando reservado, sin acción", session_id)
        return ("Este comando está reservado para administradores.", None)

    if session_id is None or session_id < 0:
        raise ValueError("session_id es requerido (entero no negativo)")

    config = config or VentasConfig()
    _empresa_id = str(id_empresa)

    # Registrar request por empresa
    CHAT_REQUESTS.labels(empresa_id=_empresa_id).inc()

    # Backpressure: limitar invocaciones concurrentes al agente (OpenAI + tools)
    async with _semaphore:
        try:
            agent = await _get_agent(id_empresa, api_key, config)
        except Exception as e:
            logger.error("[AGENT] Error obteniendo agente id_empresa=%s: %s", id_empresa, e, exc_info=True)
            record_chat_error("agent_creation_error")
            return ("Disculpa, tuve un problema de configuración. ¿Podrías intentar nuevamente?", None)

        agent_context = _prepare_agent_context(id_empresa, session_id)
        run_config = {"configurable": {"thread_id": str(session_id)}}

        # Session lock: serializa requests concurrentes del mismo usuario
        session_lock = acquire_session_lock(session_id)

        try:
            with track_chat_response():
                async with session_lock:
                    logger.debug("[AGENT] Invocando agente — session=%s, empresa=%s", session_id, id_empresa)

                    with track_llm_call():
                        result = await agent.ainvoke(
                            {"messages": [{"role": "user", "content": _build_content(message)}]},
                            config=run_config,
                            context=agent_context,
                        )

                structured = result.get("structured_response")
                if isinstance(structured, VentasStructuredResponse):
                    if structured.reply is None:
                        logger.warning("[AGENT] structured.reply es None - Session: %s", session_id)
                        reply = "No recibí respuesta del asistente, por favor intenta nuevamente."
                    elif structured.reply == "":
                        logger.warning("[AGENT] structured.reply es string vacío - Session: %s", session_id)
                        reply = "El asistente envió una respuesta vacía, por favor intenta nuevamente."
                    else:
                        reply = structured.reply
                    url = structured.url if (structured.url and structured.url.strip()) else None
                else:
                    logger.warning("[AGENT] Respuesta fuera de formato estructurado - Session: %s", session_id)
                    messages = result.get("messages", [])
                    if messages:
                        last_message = messages[-1]
                        reply = last_message.content if hasattr(last_message, "content") else str(last_message)
                        if not reply:
                            logger.warning("[AGENT] last_message.content vacío - Session: %s", session_id)
                            reply = "El asistente respondió en un formato inesperado, por favor intenta nuevamente."
                    else:
                        reply = "El asistente respondió en un formato inesperado, por favor intenta nuevamente."
                    url = None

                # Extraer tokens de todos los AIMessage
                _input_tokens = 0
                _output_tokens = 0
                for msg in result.get("messages", []):
                    um = getattr(msg, "usage_metadata", None)
                    if um:
                        _input_tokens += um.get("input_tokens", 0)
                        _output_tokens += um.get("output_tokens", 0)
                if _input_tokens or _output_tokens:
                    record_token_usage(_empresa_id, _input_tokens, _output_tokens)
                    logger.debug("[AGENT] Tokens — input=%s, output=%s, total=%s, empresa=%s",
                                 _input_tokens, _output_tokens, _input_tokens + _output_tokens, _empresa_id)

                logger.debug("[AGENT] Respuesta generada: %s...", (reply[:200], url))

        except tuple(_OPENAI_ERRORS.keys()) as e:
            log_level, error_key, log_tag, user_msg = _OPENAI_ERRORS[type(e)]
            getattr(logger, log_level)("[AGENT][%s] Session: %s | %s", log_tag, session_id, e)
            record_chat_error(error_key)
            return (user_msg, None)

        except Exception as e:
            logger.error("[AGENT] Error ejecutando agente session=%s: %s", session_id, e, exc_info=True)
            record_chat_error("agent_execution_error")
            return ("Disculpa, tuve un problema al procesar tu mensaje. ¿Podrías intentar nuevamente?", None)

        return (reply, url)
