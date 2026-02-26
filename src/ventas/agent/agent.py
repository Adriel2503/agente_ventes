"""
Lógica del agente especializado en venta directa usando LangChain 1.2+ API moderna.

Diseño de cache:
  - _model: singleton del cliente LLM, creado una sola vez al arrancar.
  - _agent_cache: TTLCache keyed by id_empresa. Un agente por empresa sirve a
    todos los usuarios usando distintos thread_ids en el checkpointer
    (InMemorySaver). TTL configurable vía AGENT_CACHE_TTL_MINUTES.
  - _agent_cache_locks: Lock por cache_key para anti-thundering herd (patrón agent_citas).
    Si N requests llegan en cache miss simultáneo para la misma empresa,
    serializan via lock; solo el primero construye, los demás hacen double-check.
  - _session_locks: Lock por session_id para serializar requests concurrentes del
    mismo usuario (evita race conditions en el checkpointer LangGraph).
"""

import asyncio
import re
from dataclasses import dataclass
from typing import Any

from cachetools import TTLCache
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel

try:
    from .. import config as app_config
    from ..tool.tools import AGENT_TOOLS
    from ..logger import get_logger
    from ..metrics import AGENT_CACHE, track_chat_response, track_llm_call, chat_requests_total, record_chat_error
    from ..prompts import build_ventas_system_prompt
except ImportError:
    from ventas import config as app_config
    from ventas.tool.tools import AGENT_TOOLS
    from ventas.logger import get_logger
    from ventas.metrics import AGENT_CACHE, track_chat_response, track_llm_call, chat_requests_total, record_chat_error
    from ventas.prompts import build_ventas_system_prompt

logger = get_logger(__name__)


class VentasStructuredResponse(BaseModel):
    """Schema para response_format. reply obligatorio; url opcional (ej. video/imagen de saludo)."""

    reply: str
    url: str | None = None


# ---------------------------------------------------------------------------
# Singletons de módulo
# ---------------------------------------------------------------------------

_checkpointer = InMemorySaver()

# Modelo LLM: una sola instancia para todo el proceso.
# init_chat_model es síncrono; no hay riesgo de race condition en asyncio.
_model = None

# Cache de agentes: id_empresa → instancia de agente.
# Tamaño y TTL configurables sin redeployar (AGENT_CACHE_MAXSIZE, AGENT_CACHE_TTL_MINUTES).
_agent_cache: TTLCache = TTLCache(
    maxsize=app_config.AGENT_CACHE_MAXSIZE,
    ttl=app_config.AGENT_CACHE_TTL_MINUTES * 60,
)

# Lock por cache_key para anti-thundering herd en construcción de agente.
# Limpiado en finally de _get_agent → no acumula entradas.
_agent_cache_locks: dict[int, asyncio.Lock] = {}
_LOCKS_CLEANUP_THRESHOLD = 750  # 1.5x maxsize=500

# Session locks: serializa requests concurrentes del mismo usuario en ainvoke.
_session_locks: dict[int, asyncio.Lock] = {}
_SESSION_LOCKS_CLEANUP_THRESHOLD = 500


# ---------------------------------------------------------------------------
# Contexto runtime inyectado en las tools
# ---------------------------------------------------------------------------

@dataclass
class AgentContext:
    """Contexto runtime para el agente (inyectado en las tools)."""
    id_empresa: int
    session_id: int = 0


# ---------------------------------------------------------------------------
# Cleanup de locks obsoletos
# ---------------------------------------------------------------------------

def _cleanup_stale_agent_locks(current_cache_key: int) -> None:
    """Elimina locks de agent_cache que ya no tienen entrada en el cache."""
    if len(_agent_cache_locks) < _LOCKS_CLEANUP_THRESHOLD:
        return
    stale = [k for k in list(_agent_cache_locks) if k not in _agent_cache and k != current_cache_key]
    for k in stale:
        _agent_cache_locks.pop(k, None)
    if stale:
        logger.debug("[AGENT] Cleanup: %s agent locks eliminados", len(stale))


def _cleanup_stale_session_locks(current_session_id: int) -> None:
    """Elimina locks de sesión que ya no están en uso activo."""
    if len(_session_locks) < _SESSION_LOCKS_CLEANUP_THRESHOLD:
        return
    stale = [
        k for k in list(_session_locks)
        if k != current_session_id and not _session_locks[k].locked()
    ]
    for k in stale:
        _session_locks.pop(k, None)
    if stale:
        logger.debug("[AGENT] Cleanup: %s session locks eliminados", len(stale))


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _validate_context(context: dict[str, Any]) -> None:
    config_data = context.get("config", {})
    required_keys = ["id_empresa"]
    missing = [k for k in required_keys if k not in config_data or config_data[k] is None]
    if missing:
        raise ValueError(f"Context missing required keys in config: {missing}")
    logger.debug("[AGENT] Context validated: id_empresa=%s", config_data.get("id_empresa"))


def _get_model():
    """
    Retorna el modelo LLM singleton, creándolo en la primera llamada.
    init_chat_model es síncrono → no hay race condition en asyncio.
    """
    global _model
    if _model is None:
        logger.info("[AGENT] Inicializando modelo LLM: %s", app_config.OPENAI_MODEL)
        _model = init_chat_model(
            f"openai:{app_config.OPENAI_MODEL}",
            api_key=app_config.OPENAI_API_KEY,
            temperature=app_config.OPENAI_TEMPERATURE,
            max_tokens=app_config.MAX_TOKENS,
            timeout=app_config.OPENAI_TIMEOUT,
        )
    return _model


async def _build_agent_for_empresa(id_empresa: int, config: dict[str, Any]):
    """
    Construye un nuevo agente para la empresa. Se llama SOLO en cache miss.
    El agente resultante es compartido por todos los usuarios de esa empresa;
    el aislamiento de sesión lo provee el checkpointer vía thread_id.
    """
    logger.info("[AGENT] Construyendo agente para id_empresa=%s", id_empresa)
    model = _get_model()
    system_prompt = await build_ventas_system_prompt(config=config)
    agent = create_agent(
        model=model,
        tools=AGENT_TOOLS,
        system_prompt=system_prompt,
        checkpointer=_checkpointer,
        response_format=VentasStructuredResponse,
    )
    logger.info(
        "[AGENT] Agente listo para id_empresa=%s (tools=%s, TTL=%s min)",
        id_empresa, len(AGENT_TOOLS), app_config.AGENT_CACHE_TTL_MINUTES,
    )
    return agent


async def _get_agent(config: dict[str, Any]):
    """
    Retorna el agente para esta empresa.

    - Fast path (cache hit): O(1), sin I/O.
    - Slow path (cache miss): Lock por id_empresa + double-check post-lock.
      N requests concurrentes serializan; solo el primero construye.
      Mismo patrón que agent_citas.
    """
    id_empresa: int = config["id_empresa"]
    cache_key = id_empresa

    # Fast path — sin lock
    if cache_key in _agent_cache:
        AGENT_CACHE.labels(result="hit").inc()
        logger.debug("[AGENT] Cache HIT id_empresa=%s", id_empresa)
        return _agent_cache[cache_key]

    _cleanup_stale_agent_locks(cache_key)
    lock = _agent_cache_locks.setdefault(cache_key, asyncio.Lock())
    try:
        async with lock:
            # Double-check: otro request puede haber construido el agente
            # mientras esperábamos el lock
            if cache_key in _agent_cache:
                AGENT_CACHE.labels(result="hit").inc()
                logger.debug("[AGENT] Cache HIT (post-lock) id_empresa=%s", id_empresa)
                return _agent_cache[cache_key]

            AGENT_CACHE.labels(result="miss").inc()
            logger.info("[AGENT] Cache MISS id_empresa=%s — iniciando build", id_empresa)
            agent = await _build_agent_for_empresa(id_empresa, config)
            _agent_cache[cache_key] = agent
            return agent
    finally:
        _agent_cache_locks.pop(cache_key, None)


def _prepare_agent_context(context: dict[str, Any], session_id: int) -> AgentContext:
    config_data = context.get("config", {})
    return AgentContext(
        id_empresa=config_data["id_empresa"],
        session_id=session_id,
    )


# ---------------------------------------------------------------------------
# Procesamiento de contenido (texto + visión)
# ---------------------------------------------------------------------------

_IMAGE_URL_RE = re.compile(
    r"https?://\S+\.(?:jpg|jpeg|png|gif|webp)(?:\?\S*)?",
    re.IGNORECASE,
)
_MAX_IMAGES = 10  # límite de OpenAI Vision


def _build_content(message: str) -> str | list[dict]:
    """
    Devuelve string si no hay URLs de imagen (Caso 1),
    o lista de bloques OpenAI Vision si las hay (Casos 2-5).
    """
    urls = _IMAGE_URL_RE.findall(message)
    if not urls:
        return message

    urls = urls[:_MAX_IMAGES]
    text = _IMAGE_URL_RE.sub("", message).strip()

    blocks: list[dict] = []
    if text:
        blocks.append({"type": "text", "text": text})
    for url in urls:
        blocks.append({"type": "image_url", "image_url": {"url": url}})
    return blocks


# ---------------------------------------------------------------------------
# Punto de entrada público
# ---------------------------------------------------------------------------

async def process_venta_message(
    message: str,
    session_id: int,
    context: dict[str, Any],
) -> tuple[str, str | None]:
    """
    Procesa un mensaje del cliente sobre ventas usando el agente LangChain.

    El agente se obtiene del cache por id_empresa (TTL=AGENT_CACHE_TTL_MINUTES min).
    El historial de conversación se aísla por session_id via thread_id
    en el checkpointer (InMemorySaver). Los requests concurrentes del mismo
    session_id se serializan vía _session_locks para evitar race conditions.

    Args:
        message: Mensaje del cliente
        session_id: ID estable del usuario (viene del gateway)
        context: Contexto con config (id_empresa, nombre_negocio, personalidad, etc.)

    Returns:
        Tupla (reply, url). url es None cuando no hay medio que adjuntar.
    """
    if not message or not message.strip():
        return ("No recibí tu mensaje. ¿Podrías repetirlo?", None)

    if session_id is None or session_id < 0:
        raise ValueError("session_id es requerido (entero no negativo)")

    try:
        _validate_context(context)
    except ValueError as e:
        logger.error("[AGENT] Error de contexto: %s", e)
        record_chat_error("context_error")
        return (f"Error de configuración: {str(e)}", None)

    config_data = dict(context.get("config", {}))
    _empresa_id = str(config_data.get("id_empresa", "unknown"))

    # Registrar request por empresa
    chat_requests_total.labels(empresa_id=_empresa_id).inc()

    try:
        agent = await _get_agent(config_data)
    except Exception as e:
        logger.error("[AGENT] Error obteniendo agente id_empresa=%s: %s", config_data.get("id_empresa"), e, exc_info=True)
        record_chat_error("agent_creation_error")
        return ("Disculpa, tuve un problema de configuración. ¿Podrías intentar nuevamente?", None)

    agent_context = _prepare_agent_context(context, session_id)
    langgraph_config = {"configurable": {"thread_id": str(session_id)}}

    # Session lock: serializa requests concurrentes del mismo usuario
    _cleanup_stale_session_locks(session_id)
    session_lock = _session_locks.setdefault(session_id, asyncio.Lock())

    try:
        with track_chat_response():
            async with session_lock:
                logger.debug("[AGENT] Invocando agente — session=%s, empresa=%s", session_id, config_data.get("id_empresa"))

                with track_llm_call():
                    result = await agent.ainvoke(
                        {"messages": [{"role": "user", "content": _build_content(message)}]},
                        config=langgraph_config,
                        context=agent_context,
                    )

            structured = result.get("structured_response")
            if isinstance(structured, VentasStructuredResponse):
                reply = structured.reply or "Lo siento, no pude procesar tu solicitud."
                url = structured.url if (structured.url and structured.url.strip()) else None
            else:
                messages = result.get("messages", [])
                if messages:
                    last_message = messages[-1]
                    reply = (
                        last_message.content
                        if hasattr(last_message, "content")
                        else str(last_message)
                    )
                else:
                    reply = "Lo siento, no pude procesar tu solicitud."
                url = None

            logger.debug("[AGENT] Respuesta generada: %s...", (reply[:200], url))

    except Exception as e:
        logger.error("[AGENT] Error ejecutando agente session=%s: %s", session_id, e, exc_info=True)
        record_chat_error("agent_execution_error")
        return ("Disculpa, tuve un problema al procesar tu mensaje. ¿Podrías intentar nuevamente?", None)

    return (reply, url)
