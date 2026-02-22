"""
Servidor HTTP del agente especializado en venta directa.
Expone POST /api/chat compatible con el API gateway (sin orquestador).

Consumido directamente por el gateway; sin protocolo MCP.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, Dict

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel, Field
from prometheus_client import make_asgi_app

try:
    from . import config as app_config
    from .agent import process_venta_message
    from .logger import setup_logging, get_logger
    from .metrics import initialize_agent_info
    from .services.api_informacion import close_http_client
except ImportError:
    from ventas import config as app_config
    from ventas.agent import process_venta_message
    from ventas.logger import setup_logging, get_logger
    from ventas.metrics import initialize_agent_info
    from ventas.services.api_informacion import close_http_client

# Configurar logging antes de cualquier otra cosa
log_level = getattr(logging, app_config.LOG_LEVEL.upper(), logging.INFO)
setup_logging(
    level=log_level,
    log_file=app_config.LOG_FILE if app_config.LOG_FILE else None
)

logger = get_logger(__name__)

# Inicializar información del agente para métricas
initialize_agent_info(model=app_config.OPENAI_MODEL, version="2.0.0")


# ---------------------------------------------------------------------------
# Modelos Pydantic
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4096)
    session_id: int
    context: Dict[str, Any] | None = None


class ChatResponse(BaseModel):
    reply: str
    url: str | None = None


# ---------------------------------------------------------------------------
# Lifespan (cierra el cliente HTTP compartido al apagar)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def app_lifespan(app: FastAPI):
    try:
        yield
    finally:
        await close_http_client()


# ---------------------------------------------------------------------------
# Aplicación FastAPI
# ---------------------------------------------------------------------------

app = FastAPI(
    lifespan=app_lifespan,
    title="Agente Ventas - MaravIA",
    description="Servicio de agente especializado en venta directa por chat",
    version="2.0.0",
)

# Endpoint de métricas para Prometheus
app.mount("/metrics", make_asgi_app())


# ---------------------------------------------------------------------------
# Endpoint principal
# ---------------------------------------------------------------------------

@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """
    Agente especializado en venta directa.

    Recibe el mensaje del cliente y el contexto de configuración enviados
    por el API gateway, y devuelve la respuesta del agente.

    El agente maneja el flujo completo: información, pedido, envío o
    recojo, pago, comprobante y confirmación de forma autónoma.
    La memoria es automática gracias al checkpointer (InMemorySaver).

    Body:
        message: Mensaje del cliente
        session_id: ID de sesión (int, unificado con gateway)
        context: Contexto con config:
            - config.id_empresa (int, requerido)
            - config.id_chatbot (int, opcional): para FAQs
            - config.nombre_bot (str, opcional): nombre del asistente
            - config.personalidad (str, opcional)
            - config.nombre_negocio (str, opcional)
            - config.propuesta_valor (str, opcional)
            - config.medios_pago (str, opcional)

    Returns:
        JSON con campo reply y url (siempre null en ventas)
    """
    context = req.context or {}

    logger.info("[HTTP] Mensaje recibido - Session: %s, Length: %s chars", req.session_id, len(req.message))
    logger.debug("[HTTP] Message: %s...", req.message[:100])
    logger.debug("[HTTP] Context keys: %s", list(context.keys()))

    try:
        reply = await asyncio.wait_for(
            process_venta_message(
                message=req.message,
                session_id=req.session_id,
                context=context,
            ),
            timeout=app_config.CHAT_TIMEOUT,
        )

        logger.info("[HTTP] Respuesta generada - Length: %s chars", len(reply))
        logger.debug("[HTTP] Reply: %s...", reply[:200])
        return ChatResponse(reply=reply, url=None)

    except asyncio.TimeoutError:
        error_msg = f"La solicitud tardó más de {app_config.CHAT_TIMEOUT}s. Por favor, intenta de nuevo."
        logger.error("[HTTP] Timeout en process_venta_message (CHAT_TIMEOUT=%s)", app_config.CHAT_TIMEOUT)
        return ChatResponse(reply=error_msg, url=None)

    except ValueError as e:
        error_msg = f"Error de configuración: {str(e)}"
        logger.error("[HTTP] %s", error_msg)
        return ChatResponse(reply=error_msg, url=None)

    except asyncio.CancelledError:
        raise

    except Exception as e:
        error_msg = f"Error procesando mensaje: {str(e)}"
        logger.error("[HTTP] %s", error_msg, exc_info=True)
        return ChatResponse(reply=error_msg, url=None)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "agent": "ventas", "version": "2.0.0"}


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("INICIANDO SERVICIO VENTAS - MaravIA")
    logger.info("=" * 60)
    logger.info("Host: %s:%s", app_config.SERVER_HOST, app_config.SERVER_PORT)
    logger.info("Modelo: %s", app_config.OPENAI_MODEL)
    logger.info("Timeout LLM: %ss", app_config.OPENAI_TIMEOUT)
    logger.info("Timeout API: %ss", app_config.API_TIMEOUT)
    logger.info("Timeout Chat: %ss", app_config.CHAT_TIMEOUT)
    logger.info("Log Level: %s", app_config.LOG_LEVEL)
    logger.info("-" * 60)
    logger.info("Endpoint: POST /api/chat")
    logger.info("Health:   GET  /health")
    logger.info("Metrics:  GET  /metrics")
    logger.info("Tools internas del agente: search_productos_servicios")
    logger.info("=" * 60)

    uvicorn.run(
        app,
        host=app_config.SERVER_HOST,
        port=app_config.SERVER_PORT,
    )
