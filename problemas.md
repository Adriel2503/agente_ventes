# Problemas conocidos — agent_ventas

## 1. Warning: VentasStructuredResponse no registrado en LangGraph

### Problema
Al tener más de un turno de conversación, LangGraph intentaba deserializar el objeto
`VentasStructuredResponse` guardado en el checkpoint y lanzaba este warning:

```
WARNING - [jsonplus.py:530] - Deserializing unregistered type
ventas.agent.agent.VentasStructuredResponse from checkpoint.
This will be blocked in a future version.
Add to allowed_msgpack_modules to silence:
[('ventas.agent.agent', 'VentasStructuredResponse')]
```

**Causa:** `create_agent(..., response_format=VentasStructuredResponse)` hace que LangGraph
guarde el objeto Pydantic en el estado del grafo al final de cada invocación. Al recuperar
el checkpoint en el turno siguiente, el serializador no reconoce el tipo porque no está
en su lista de permitidos.

### Código original
```python
from langgraph.checkpoint.memory import InMemorySaver

_checkpointer = InMemorySaver()
```

### Código corregido
```python
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

_checkpointer = InMemorySaver(
    serde=JsonPlusSerializer(
        allowed_msgpack_modules=[("ventas.agent.agent", "VentasStructuredResponse")]
    )
)
```

**Estado:** Resuelto. Commit `f877174`.

---

## 2. Warning: PydanticSerializationUnexpectedValue en context

### Problema
En cada mensaje recibido aparece este warning en los logs:

```
UserWarning: Pydantic serializer warnings:
  PydanticSerializationUnexpectedValue(Expected `none` - serialized value may not be
  as expected [field_name='context', input_value=AgentContext(id_empresa=14,
  session_id=3796), input_type=AgentContext])
```

**Causa:** Al llamar `agent.ainvoke(..., context=agent_context)`, LangGraph internamente
usa un modelo Pydantic (`RunnableConfig`) para representar la configuración de la llamada.
Ese modelo tiene el campo `context` tipado como `None`. Al pasarle un objeto `AgentContext`
(dataclass), Pydantic no sabe serializarlo y lanza el warning.

El agente **funciona correctamente** — el contexto llega bien a las tools. Es solo un
warning de serialización.

### Código involucrado

`agent.py` — definición del contexto:
```python
from dataclasses import dataclass

@dataclass
class AgentContext:
    id_empresa: int
    session_id: int = 0
```

`agent.py` — invocación del agente:
```python
result = await agent.ainvoke(
    {"messages": [...]},
    config=langgraph_config,
    context=agent_context,   # ← aquí se origina el warning
)
```

### Posible solución
Agregar un `@field_serializer` en el schema de cada tool para que Pydantic ignore
el campo `runtime` al serializar (workaround de la comunidad, no fix oficial):

```python
from pydantic import BaseModel, ConfigDict, field_serializer
from langchain.tools import tool, ToolRuntime

class SearchProductosInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    busqueda: str
    runtime: ToolRuntime = None

    @field_serializer("runtime")
    def _serialize_runtime(self, v):
        return None

@tool(args_schema=SearchProductosInput)
async def search_productos_servicios(busqueda: str, runtime: ToolRuntime = None) -> str:
    ...
```

**Estado:** Pendiente. Bug conocido de LangChain/LangGraph sin fix oficial a la fecha
(febrero 2026). Issues relacionados:
- https://github.com/langchain-ai/langgraph/issues/6431
- https://github.com/langchain-ai/langgraph/issues/6318
- https://github.com/langchain-ai/langchain/issues/33646
