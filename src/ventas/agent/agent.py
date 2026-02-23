"""
Lógica del agente especializado en venta directa usando LangChain 1.2+ API moderna.

Diseño de cache:
  - _model: singleton del cliente LLM, creado una sola vez al arrancar.
  - _agent_cache: TTLCache keyed by id_empresa. Un agente por empresa sirve
    a todos los usuarios de esa empresa usando distintos thread_ids en el
    checkpointer (InMemorySaver). TTL configurable vía AGENT_CACHE_TTL.
  - _building: dict de asyncio.Task en vuelo para evitar thundering herd:
    si N requests llegan en cache miss simultáneo para la misma empresa,
    solo el primero construye; el resto espera ese Task.
"""

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Any

from cachetools import TTLCache
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langgraph.checkpoint.memory import InMemorySaver

try:
    from .. import config as app_config
    from ..tool.tools import AGENT_TOOLS
    from ..logger import get_logger
    from ..metrics import AGENT_CACHE, LLM_REQUESTS, LLM_DURATION
    from ..prompts import build_ventas_system_prompt
except ImportError:
    from ventas import config as app_config
    from ventas.tool.tools import AGENT_TOOLS
    from ventas.logger import get_logger
    from ventas.metrics import AGENT_CACHE, LLM_REQUESTS, LLM_DURATION
    from ventas.prompts import build_ventas_system_prompt

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Singletons de módulo
# ---------------------------------------------------------------------------

_checkpointer = InMemorySaver()

# Modelo LLM: una sola instancia para todo el proceso.
# init_chat_model es síncrono; no hay riesgo de race condition en asyncio.
_model = None

# Cache de agentes: id_empresa → instancia de agente.
# Tamaño y TTL configurables sin redeployar (AGENT_CACHE_MAXSIZE, AGENT_CACHE_TTL).
_agent_cache: TTLCache = TTLCache(
    maxsize=app_config.AGENT_CACHE_MAXSIZE,
    ttl=app_config.AGENT_CACHE_TTL,
)

# Tasks en vuelo por empresa (anti-thundering herd).
_building: dict[int, asyncio.Task] = {}


# ---------------------------------------------------------------------------
# Contexto runtime inyectado en las tools
# ---------------------------------------------------------------------------

@dataclass
class AgentContext:
    """Contexto runtime para el agente (inyectado en las tools)."""
    id_empresa: int
    session_id: int = 0


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
    )
    logger.info(
        "[AGENT] Agente listo para id_empresa=%s (tools=%s, TTL=%ss)",
        id_empresa, len(AGENT_TOOLS), app_config.AGENT_CACHE_TTL,
    )
    return agent


async def _get_agent(config: dict[str, Any]):
    """
    Retorna el agente para esta empresa.

    - Fast path (cache hit): O(1), sin I/O.
    - Slow path (cache miss): construye el agente y lo cachea.
    - Anti-thundering herd: N requests concurrentes en miss comparten
      un único asyncio.Task; solo uno construye, los demás esperan.
    """
    id_empresa: int = config["id_empresa"]

    # Fast path
    if id_empresa in _agent_cache:
        AGENT_CACHE.labels(result="hit").inc()
        logger.debug("[AGENT] Cache HIT id_empresa=%s", id_empresa)
        return _agent_cache[id_empresa]

    # Si ya hay un build en curso para esta empresa, esperar ese Task
    if id_empresa in _building:
        logger.debug("[AGENT] Esperando build en curso id_empresa=%s", id_empresa)
        return await asyncio.shield(_building[id_empresa])

    # Iniciar build y registrar el Task para que otros puedan unirse
    AGENT_CACHE.labels(result="miss").inc()
    logger.info("[AGENT] Cache MISS id_empresa=%s — iniciando build", id_empresa)
    task = asyncio.create_task(_build_agent_for_empresa(id_empresa, config))
    _building[id_empresa] = task
    try:
        agent = await task
        _agent_cache[id_empresa] = agent
        return agent
    finally:
        # Limpiar siempre, incluso si el build falló
        _building.pop(id_empresa, None)


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
) -> str:
    """
    Procesa un mensaje del cliente sobre ventas usando el agente LangChain.

    El agente se obtiene del cache por id_empresa (TTL=AGENT_CACHE_TTL).
    El historial de conversación se aísla por session_id via thread_id
    en el checkpointer (InMemorySaver).

    Args:
        message: Mensaje del cliente
        session_id: ID estable del usuario (viene del gateway)
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

    config_data = dict(context.get("config", {}))

    try:
        agent = await _get_agent(config_data)
    except Exception as e:
        logger.error("[AGENT] Error obteniendo agente id_empresa=%s: %s", config_data.get("id_empresa"), e, exc_info=True)
        return "Disculpa, tuve un problema de configuración. ¿Podrías intentar nuevamente?"

    agent_context = _prepare_agent_context(context, session_id)
    langgraph_config = {"configurable": {"thread_id": str(session_id)}}

    _llm_start = time.perf_counter()
    _llm_status = "success"

    try:
        logger.debug("[AGENT] Invocando agente — session=%s, empresa=%s", session_id, config_data.get("id_empresa"))

        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": _build_content(message)}]},
            config=langgraph_config,
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
        _llm_status = "error"
        logger.error("[AGENT] Error ejecutando agente session=%s: %s", session_id, e, exc_info=True)
        return "Disculpa, tuve un problema al procesar tu mensaje. ¿Podrías intentar nuevamente?"

    finally:
        LLM_REQUESTS.labels(status=_llm_status).inc()
        LLM_DURATION.observe(time.perf_counter() - _llm_start)

    return response_text
