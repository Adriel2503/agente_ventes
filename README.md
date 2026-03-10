# Agente Ventas — MaravIA

Agente especializado en ventas directas diseñado para operar dentro del ecosistema de **MaravIA**. El API gateway lo consume directamente (sin orquestador) y guía al cliente a través del flujo completo de compra: búsqueda de productos, selección, modalidad de entrega, pago y confirmación.

## Tabla de contenidos

- [Descripción general](#descripción-general)
- [Arquitectura](#arquitectura)
- [Estructura del proyecto](#estructura-del-proyecto)
- [Requisitos](#requisitos)
- [Configuración](#configuración)
- [Ejecución](#ejecución)
  - [Local (uv)](#local-uv)
  - [Docker](#docker)
- [API HTTP expuesta](#api-http-expuesta)
- [Flujo de ventas](#flujo-de-ventas)
- [Herramientas internas del agente](#herramientas-internas-del-agente)
- [Servicios externos](#servicios-externos)
- [Resiliencia](#resiliencia)
- [Observabilidad](#observabilidad)
- [Características destacadas](#características-destacadas)

---

## Descripción general

El Agente Ventas es un microservicio HTTP que expone `POST /api/chat` al API gateway. Internamente utiliza:

- **LangChain + LangGraph** para la lógica del agente con memoria de sesión
- **OpenAI** (GPT-4o-mini por defecto) como LLM principal
- **FastAPI + uvicorn** como servidor HTTP
- **Jinja2** para la generación dinámica del system prompt
- **httpx + tenacity** como cliente HTTP con retry y backoff exponencial
- **Circuit breaker** por API externa, particionado por empresa
- **Prometheus** para métricas de observabilidad (`/metrics`)
- **API MaravIA** para categorías, sucursales, métodos de pago, costos de envío y búsqueda de productos

---

## Arquitectura

```
API Gateway (Go)
    │
    │  POST /api/chat { message, session_id, context }
    ▼
┌───────────────────────────────────────────────┐
│  FastAPI Service  (puerto 8001)               │
│                                               │
│  process_venta_message()                      │
│    ├─ Validar contexto (id_empresa)           │
│    ├─ Build system prompt (Jinja2 + gather)   │
│    │    ├─ obtener_categorias()  ────────────►│ API MaravIA
│    │    ├─ obtener_sucursales()  ────────────►│ API MaravIA
│    │    ├─ obtener_metodos_pago() ───────────►│ API MaravIA
│    │    ├─ fetch_contexto_negocio() ─────────►│ API MaravIA
│    │    ├─ fetch_costo_envio() ──────────────►│ API MaravIA
│    │    └─ fetch_preguntas_frecuentes() ─────►│ API FAQs
│    │                                          │
│    └─ LangChain Agent (TTLCache por empresa)  │
│         ├─ OpenAI Chat Model (singleton)      │
│         ├─ InMemorySaver (thread_id=session)  │
│         └─ Tools:                             │
│              ├─ search_productos_servicios ───►│ API MaravIA
│              ├─ registrar_pedido_delivery ────►│ API MaravIA
│              └─ registrar_pedido_sucursal ────►│ API MaravIA
└───────────────────────────────────────────────┘
    │
    │  { reply, url }
    ▼
API Gateway
```

---

## Estructura del proyecto

```
agent_ventas/
├── src/ventas/
│   ├── __init__.py                  # __version__ (desde pyproject.toml)
│   ├── main.py                      # FastAPI app, POST /api/chat, lifespan
│   ├── logger.py                    # setup_logging, get_logger
│   ├── metrics.py                   # Métricas Prometheus (Counters, Histograms, Info)
│   │
│   ├── config/
│   │   ├── config.py                # Variables de entorno con validación de tipos
│   │   └── circuit_breakers.py      # Instancias de CB por API (informacion_cb, preguntas_cb)
│   │
│   ├── infra/                       # Infraestructura transversal (agnostic, sin lógica de negocio)
│   │   ├── http_client.py           # httpx.AsyncClient singleton + tenacity retry
│   │   ├── circuit_breaker.py       # Clase CircuitBreaker genérica (TTLCache-based)
│   │   └── _resilience.py           # resilient_call: CB wrapper para llamadas HTTP
│   │
│   ├── agent/
│   │   ├── agent.py                 # Core: model singleton, agent TTLCache, session locks
│   │   └── prompts/
│   │       ├── __init__.py          # build_ventas_system_prompt (6 APIs en gather)
│   │       └── ventas_system.j2     # Template Jinja2 del system prompt
│   │
│   ├── services/
│   │   ├── busqueda_productos.py    # Búsqueda en catálogo (cache 15min + circuit breaker)
│   │   ├── registrar_pedido.py      # Registro de pedido (delivery / sucursal)
│   │   └── prompt_data/             # Datos para el system prompt (todos con TTLCache 1h)
│   │       ├── categorias.py
│   │       ├── sucursales.py
│   │       ├── metodos_pago.py
│   │       ├── costo_envio.py
│   │       ├── contexto_negocio.py
│   │       └── preguntas_frecuentes.py
│   │
│   └── tool/
│       └── tools.py                 # AGENT_TOOLS: search, pedido_delivery, pedido_sucursal
│
├── pyproject.toml                   # Build (hatchling), dependencias, versión
├── Dockerfile                       # Python 3.12 slim + uv
├── compose.yaml                     # Docker Compose
├── run.py                           # Script de entrada (python run.py)
├── .env.example                     # Plantilla de configuración
└── .gitignore
```

---

## Requisitos

- Python **3.12+**
- **uv** como gestor de paquetes (usado en Docker y desarrollo local)
- Cuenta de **OpenAI** con API Key
- Acceso a la **API MaravIA** (`ws_informacion_ia.php`)
- Docker (opcional, para despliegue en contenedor)

---

## Configuración

Copia `.env.example` a `.env` y completa los valores:

```bash
cp .env.example .env
```

### Variables principales

| Variable | Default | Descripción |
|---|---|---|
| `OPENAI_API_KEY` | *(requerido)* | Clave de API de OpenAI |
| `OPENAI_MODEL` | `gpt-4o-mini` | Modelo de OpenAI a usar |
| `OPENAI_TEMPERATURE` | `0.5` | Temperatura del modelo (0.0–2.0) |
| `OPENAI_TIMEOUT` | `60` | Timeout por llamada a OpenAI (segundos) |
| `MAX_TOKENS` | `2048` | Máximo de tokens por respuesta |
| `SERVER_HOST` | `0.0.0.0` | Host del servidor |
| `SERVER_PORT` | `8001` | Puerto del servidor |
| `LOG_LEVEL` | `INFO` | Nivel de logging (DEBUG, INFO, WARNING, ERROR) |
| `LOG_FILE` | *(vacío)* | Ruta de archivo de log (vacío = solo consola) |

### Timeouts y límites

| Variable | Default | Descripción |
|---|---|---|
| `API_TIMEOUT` | `10` | Timeout por request HTTP a APIs externas (segundos) |
| `CHAT_TIMEOUT` | `120` | Timeout global por mensaje (debe ser >= OPENAI_TIMEOUT) |

### Cache del agente

| Variable | Default | Descripción |
|---|---|---|
| `AGENT_CACHE_TTL_MINUTES` | `60` | TTL del cache de agentes por empresa (minutos) |
| `AGENT_CACHE_MAXSIZE` | `500` | Máximo de empresas en cache simultáneamente |

### Resiliencia HTTP

| Variable | Default | Descripción |
|---|---|---|
| `HTTP_RETRY_ATTEMPTS` | `3` | Reintentos por llamada HTTP fallida |
| `HTTP_RETRY_WAIT_MIN` | `1` | Espera mínima entre reintentos (segundos) |
| `HTTP_RETRY_WAIT_MAX` | `4` | Espera máxima entre reintentos (segundos) |
| `CB_THRESHOLD` | `3` | Fallos consecutivos para abrir el circuit breaker |
| `CB_RESET_TTL` | `300` | Tiempo para resetear el CB tras apertura (segundos) |

### APIs externas

| Variable | Default | Descripción |
|---|---|---|
| `API_INFORMACION_URL` | `https://api.maravia.pe/.../ws_informacion_ia.php` | Endpoint principal MaravIA |
| `API_PREGUNTAS_FRECUENTES_URL` | `https://api.maravia.pe/.../ws_preguntas_frecuentes.php` | Endpoint de FAQs |
| `TIMEZONE` | `America/Lima` | Zona horaria para fechas |

> **Nota:** `CHAT_TIMEOUT` debe ser mayor o igual que `OPENAI_TIMEOUT` para evitar cancelaciones prematuras.

---

## Ejecución

### Local (uv)

```bash
# 1. Instalar uv (si no lo tienes)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Configurar variables de entorno
cp .env.example .env
# Editar .env con tu OPENAI_API_KEY

# 3. Iniciar el servidor
uv run python run.py
```

### Docker

```bash
# Construir y levantar el contenedor
docker compose up --build

# En segundo plano
docker compose up -d
```

La imagen usa Python 3.12 slim con `uv` como gestor de paquetes, usuario no-root y zona horaria `America/Lima`.

---

## API HTTP expuesta

### `POST /api/chat`

Endpoint principal consumido por el API gateway.

**Request body:**

```json
{
  "message": "Quiero comprar zapatos talla 40",
  "session_id": 1234,
  "context": {
    "config": {
      "id_empresa": 123,
      "id_chatbot": 7,
      "nombre_bot": "Valeria",
      "personalidad": "...",
      "nombre_negocio": "Mi Tienda",
      "propuesta_valor": "...",
      "medios_pago": "..."
    }
  }
}
```

| Campo | Tipo | Descripción |
|---|---|---|
| `message` | `str` | Mensaje del usuario (puede incluir URLs de imágenes) |
| `session_id` | `int` | ID estable del usuario (viene del gateway, nunca cambia) |
| `context.config.id_empresa` | `int` | **Requerido.** ID de la empresa |
| `context.config.id_chatbot` | `int` | Opcional. Para cargar FAQs del chatbot |
| `context.config.nombre_bot` | `str` | Opcional. Nombre del asistente virtual |
| `context.config.personalidad` | `str` | Opcional. Instrucciones de personalidad |
| `context.config.nombre_negocio` | `str` | Opcional. Nombre del negocio |
| `context.config.propuesta_valor` | `str` | Opcional. Propuesta de valor del negocio |
| `context.config.medios_pago` | `str` | Opcional. Info adicional de medios de pago |

**Response:**

```json
{
  "reply": "¡Hola! Con gusto te ayudo a encontrar zapatos...",
  "url": null
}
```

### `GET /health`

Health check con detección de degradación (circuit breakers abiertos, API key faltante).

```json
{ "status": "ok", "agent": "ventas", "version": "2.5.0", "issues": [] }
```

Retorna `200` si todo está OK, `503` si hay problemas (con detalle en `issues`).

### `GET /metrics`

Métricas en formato Prometheus (ver sección [Observabilidad](#observabilidad)).

---

## Flujo de ventas

El system prompt define un flujo de ventas estructurado en dos ramas:

```
Inicio
  └─► Relevamiento de necesidades
        └─► Búsqueda / selección de productos
              ├─► RAMA A: Delivery
              │     ├─ Distrito de entrega
              │     ├─ Tipo de delivery (Express / Normal)
              │     ├─ Resumen y confirmación
              │     ├─ Método de pago
              │     ├─ Validación de comprobante (imagen)
              │     ├─ Dirección y referencia
              │     ├─ Datos del cliente (nombre, DNI, celular)
              │     └─► registrar_pedido_delivery
              │
              └─► RAMA B: Recojo en tienda
                    ├─ Selección de sucursal
                    ├─ Resumen y confirmación
                    ├─ Método de pago
                    ├─ Validación de comprobante (imagen)
                    ├─ Datos del cliente (nombre, DNI, celular)
                    └─► registrar_pedido_sucursal
```

---

## Herramientas internas del agente

### `search_productos_servicios`

Busca productos y servicios en el catálogo de la empresa en tiempo real.

| Parámetro | Tipo | Descripción |
|---|---|---|
| `busqueda` | `str` | Término de búsqueda (ej: "laptop", "juego de mesa") |

Retorna una lista formateada con nombre, precio, descripción y ID de cada producto.

### `registrar_pedido_delivery`

Registra un pedido con envío a domicilio. Se invoca cuando el cliente completó todo el flujo de delivery.

| Parámetro | Tipo | Descripción |
|---|---|---|
| `productos` | `list[{id_catalogo, cantidad}]` | Productos seleccionados |
| `operacion` | `str` | Código de operación del comprobante |
| `tipo_envio` | `str` | Tipo de envío (Express, Normal) |
| `direccion` | `str` | Dirección de entrega |
| `costo_envio` | `float` | Costo del envío |
| `fecha_entrega_estimada` | `str` | Fecha estimada (YYYY-MM-DD) |
| `nombre` | `str` | Nombre del cliente |
| `dni` | `int` | DNI del cliente |
| `celular` | `int` | Teléfono del cliente |
| `medio_pago` | `str` | Medio de pago usado |
| `monto_pagado` | `float` | Monto pagado |
| `email` | `str` | Correo del cliente (opcional) |
| `observacion` | `str` | Nota adicional (opcional) |

### `registrar_pedido_sucursal`

Registra un pedido con recojo en sucursal. Mismos datos de cliente, sin campos de envío.

| Parámetro | Tipo | Descripción |
|---|---|---|
| `productos` | `list[{id_catalogo, cantidad}]` | Productos seleccionados |
| `operacion` | `str` | Código de operación del comprobante |
| `sucursal` | `str` | Nombre exacto de la sucursal |
| `nombre`, `dni`, `celular`, `medio_pago`, `monto_pagado` | — | Datos del cliente |
| `email`, `observacion` | — | Opcionales |

---

## Servicios externos

Todos los servicios (excepto FAQs) consumen `API_INFORMACION_URL` mediante un código de operación (`codOpe`):

| Servicio | `codOpe` | Cache | Descripción |
|---|---|---|---|
| `obtener_categorias()` | `OBTENER_CATEGORIAS` | TTL 1h | Categorías del catálogo |
| `obtener_sucursales()` | `OBTENER_SUCURSALES_PUBLICAS` | TTL 1h | Sucursales con dirección y horarios |
| `obtener_metodos_pago()` | `OBTENER_METODOS_PAGO` | TTL 1h | Bancos y billeteras digitales |
| `fetch_contexto_negocio()` | `OBTENER_CONTEXTO_NEGOCIO` | TTL 1h | Contexto del negocio |
| `fetch_costo_envio()` | `OBTENER_COSTO_ENVIO` | TTL 1h | Costos de envío por zona |
| `buscar_productos_servicios()` | `BUSCAR_PRODUCTOS_SERVICIOS_VENTAS_DIRECTAS` | TTL 15min | Búsqueda en catálogo |
| `registrar_pedido()` | `REGISTRAR_PEDIDO` | — | Registro de pedido (sin retry, escritura) |
| `fetch_preguntas_frecuentes()` | *(endpoint FAQs)* | TTL 1h | FAQs por id_chatbot |

El cliente HTTP es un `httpx.AsyncClient` compartido (singleton lazy-init) que se cierra limpiamente al apagar el servidor via lifespan.

---

## Resiliencia

### Retry con backoff exponencial

Cada llamada HTTP usa `tenacity` con retry automático:
- **3 intentos** (configurable vía `HTTP_RETRY_ATTEMPTS`)
- Backoff exponencial entre 1s y 4s
- Solo reintenta en errores de red (`httpx.TransportError`)

### Circuit breaker

Cada API externa tiene su propio circuit breaker particionado por `id_empresa`:
- **Threshold**: 3 fallos consecutivos abren el circuito
- **Reset TTL**: 300 segundos (auto-reset vía TTLCache)
- Circuito abierto = respuesta inmediata sin tocar la red

### Degradación graceful

Si una API falla al construir el system prompt, el agente continúa funcionando con valores por defecto. El health check (`GET /health`) reporta `"status": "degraded"` con detalle de qué API está afectada.

---

## Observabilidad

### Métricas Prometheus (`GET /metrics`)

| Métrica | Tipo | Labels | Descripción |
|---|---|---|---|
| `ventas_http_requests_total` | Counter | `status` | Requests al endpoint /api/chat |
| `ventas_http_duration_seconds` | Histogram | — | Latencia total del endpoint |
| `ventas_llm_requests_total` | Counter | `status` | Invocaciones al agente LLM |
| `ventas_llm_duration_seconds` | Histogram | — | Latencia de agent.ainvoke |
| `ventas_chat_response_duration_seconds` | Histogram | `status` | Latencia total de procesamiento |
| `ventas_chat_requests_total` | Counter | `empresa_id` | Requests por empresa |
| `ventas_chat_errors_total` | Counter | `error_type` | Errores por tipo |
| `ventas_agent_cache_total` | Counter | `result` | Cache de agentes (hit/miss) |
| `ventas_tool_calls_total` | Counter | `tool`, `status` | Invocaciones de tools |
| `ventas_search_cache_total` | Counter | `result` | Cache de búsqueda |
| `agent_ventas_info` | Info | — | Versión, modelo, tipo de agente |

### Logging estructurado

Prefijos por módulo para facilitar el rastreo en producción:

| Prefijo | Módulo |
|---|---|
| `[HTTP]` | main.py (endpoint) |
| `[AGENT]` | agent.py (core) |
| `[TOOL]` | tools.py (herramientas) |
| `[BUSQUEDA]` | busqueda_productos.py |
| `[REGISTRAR_PEDIDO]` | registrar_pedido.py |
| `[CATEGORIAS]`, `[SUCURSALES]`, etc. | prompt_data/* |

Nivel y destino configurables vía `LOG_LEVEL` y `LOG_FILE`.

---

## Características destacadas

### Visión multimodal
El agente detecta automáticamente URLs de imágenes en los mensajes del usuario (jpg, jpeg, png, gif, webp) y las envía al modelo como bloques de visión de OpenAI. Esto permite validar comprobantes de pago enviados como capturas de pantalla.

### System prompt dinámico
El prompt se genera con datos reales del negocio obtenidos en paralelo (`asyncio.gather`): categorías, sucursales, métodos de pago, costos de envío, contexto del negocio y FAQs. Cada dato tiene su propio cache TTL y circuit breaker.

### Cache de agentes multiempresa
Un agente por empresa en `TTLCache` (TTL configurable). Todos los usuarios de una empresa comparten el agente; el aislamiento de sesión lo provee el checkpointer vía `thread_id=session_id`. Anti-thundering herd con lock + double-check por empresa.

### Respuesta estructurada
El agente retorna `{reply, url}` vía `response_format=VentasStructuredResponse`. El campo `url` permite adjuntar imágenes o videos (ej: video de saludo personalizado).

### Versión unificada
La versión se define una sola vez en `pyproject.toml` y se lee en runtime vía `importlib.metadata`. Aparece en: FastAPI docs, health check, métricas Prometheus y banner de startup.
