"""
Servidor HTTP del agente especializado en venta directa.
Expone POST /api/chat compatible con el API gateway (sin orquestador).

Consumido directamente por el gateway; sin protocolo MCP.
"""

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from prometheus_client import make_asgi_app

from . import config as app_config, __version__
from .agent import process_venta_message, init_checkpointer, close_checkpointer
from .schemas import ChatRequest, ChatResponse
from .logger import setup_logging, get_logger, trace_id
from .metrics import initialize_agent_info, HTTP_REQUESTS, HTTP_DURATION
from .infra import close_http_client
from .config import informacion_cb, preguntas_cb

# Configurar logging antes de cualquier otra cosa
log_level = getattr(logging, app_config.LOG_LEVEL.upper(), logging.INFO)
setup_logging(
    level=log_level,
    log_file=app_config.LOG_FILE if app_config.LOG_FILE else None
)

logger = get_logger(__name__)

# Inicializar información del agente para métricas
initialize_agent_info(model=app_config.OPENAI_MODEL, version=__version__)


# ---------------------------------------------------------------------------
# Lifespan (cierra el cliente HTTP compartido al apagar)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def app_lifespan(app: FastAPI):
    await init_checkpointer()
    try:
        yield
    finally:
        await close_checkpointer()
        await close_http_client()


# ---------------------------------------------------------------------------
# Aplicación FastAPI
# ---------------------------------------------------------------------------

app = FastAPI(
    lifespan=app_lifespan,
    title="Agente Ventas - MaravIA",
    description="Servicio de agente especializado en venta directa por chat",
    version=__version__,
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
        id_empresa: ID de la empresa (int, requerido)
        config: Configuración opcional del bot (VentasConfig):
            - id_chatbot (int, opcional): para FAQs
            - nombre_bot (str, opcional): nombre del asistente
            - personalidad (str, opcional)
            - nombre_negocio (str, opcional)
            - propuesta_valor (str, opcional)
            - medios_pago (str, opcional)

    Returns:
        JSON con campo reply (texto del agente) y url (opcional, ej. video/imagen de saludo)
    """
    trace_id.set(uuid.uuid4().hex[:8])
    config = req.config

    logger.info("[HTTP] Mensaje recibido - Session: %s, Empresa: %s, Length: %s chars", req.session_id, req.id_empresa, len(req.message))
    logger.debug("[HTTP] Message: %s...", req.message[:100])
    logger.debug("[HTTP] Config fields: %s", config.model_fields_set if config else "None")

    _start = time.perf_counter()
    _http_status = "success"

    try:
        reply, url = await asyncio.wait_for(
            process_venta_message(
                message=req.message,
                session_id=req.session_id,
                id_empresa=req.id_empresa,
                api_key=req.api_key,
                config=config,
            ),
            timeout=app_config.CHAT_TIMEOUT,
        )

        logger.info("[HTTP] Respuesta generada - Length: %s chars", len(reply))
        logger.debug("[HTTP] Reply: %s...", reply[:200])
        return ChatResponse(reply=reply, url=url)

    except asyncio.TimeoutError:
        _http_status = "timeout"
        error_msg = f"La solicitud tardó más de {app_config.CHAT_TIMEOUT}s. Por favor, intenta de nuevo."
        logger.error("[HTTP] Timeout en process_venta_message (CHAT_TIMEOUT=%s)", app_config.CHAT_TIMEOUT)
        return ChatResponse(reply=error_msg, url=None)

    except ValueError as e:
        _http_status = "error"
        error_msg = f"Error de configuración: {str(e)}"
        logger.error("[HTTP] %s", error_msg)
        return ChatResponse(reply=error_msg, url=None)

    except asyncio.CancelledError:
        _http_status = None  # No contar requests abortados externamente
        raise

    except Exception as e:
        _http_status = "error"
        error_msg = f"Error procesando mensaje: {str(e)}"
        logger.error("[HTTP] %s", error_msg, exc_info=True)
        return ChatResponse(reply=error_msg, url=None)

    finally:
        if _http_status is not None:
            HTTP_REQUESTS.labels(status=_http_status).inc()
            HTTP_DURATION.observe(time.perf_counter() - _start)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    issues = []
    if informacion_cb.any_open():
        issues.append("informacion_api_degraded")
    if preguntas_cb.any_open():
        issues.append("preguntas_api_degraded")
    status = "degraded" if issues else "ok"
    return JSONResponse(
        status_code=503 if issues else 200,
        content={"status": status, "agent": "ventas", "version": __version__, "issues": issues},
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main():
    logger.info("=" * 60)
    logger.info("INICIANDO SERVICIO VENTAS - MaravIA")
    logger.info("=" * 60)
    logger.info("Host: %s:%s", app_config.SERVER_HOST, app_config.SERVER_PORT)
    logger.info("Modelo: %s", app_config.OPENAI_MODEL)
    logger.info("Timeout LLM: %ss", app_config.OPENAI_TIMEOUT)
    logger.info("Timeout API: %ss", app_config.API_TIMEOUT)
    logger.info("Cache TTL agente:   %s min", app_config.AGENT_CACHE_TTL_MINUTES)
    logger.info("Max mensajes LLM:   %s", app_config.MAX_MESSAGES_HISTORY)
    logger.info("Timeout chat:       %ss", app_config.CHAT_TIMEOUT)
    logger.info("Timezone: %s", app_config.TIMEZONE)
    logger.info("Circuit breaker threshold: %s fallos", app_config.CB_THRESHOLD)
    logger.info("Redis checkpointer: %s", "activo" if app_config.REDIS_URL else "InMemorySaver")
    logger.info("Log Level: %s", app_config.LOG_LEVEL)
    logger.info("-" * 60)
    logger.info("Endpoint: POST /api/chat")
    logger.info("Health:   GET  /health")
    logger.info("Metrics:  GET  /metrics")
    logger.info("Tools internas del agente:")
    logger.info("- search_productos_servicios (busca productos/servicios)")
    logger.info("- registrar_pedido_delivery (registra pedido con envio)")
    logger.info("- registrar_pedido_sucursal (registra pedido con recojo)")
    logger.info("=" * 60)

    uvicorn.run(
        app,
        host=app_config.SERVER_HOST,
        port=app_config.SERVER_PORT,
    )


if __name__ == "__main__":
    main()
