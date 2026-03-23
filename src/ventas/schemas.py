"""
Modelos Pydantic del agente.
Define el contrato HTTP (request/response) y la configuración tipada.
"""

from pydantic import BaseModel, Field, field_validator

from .logger import get_logger

logger = get_logger(__name__)


class VentasConfig(BaseModel):
    """Configuración específica del agente de ventas."""

    # --- Campos para prompts (Jinja2 template) ---
    id_chatbot: int | None = None
    nombre_bot: str | None = None
    nombre_negocio: str | None = None
    personalidad: str = "amable, profesional y cercano"
    propuesta_valor: str | None = None
    medios_pago: str | None = None
    frase_saludo: str | None = None
    frase_no_sabe: str | None = None
    frase_des: str | None = None
    archivo_saludo: str | None = None

    @field_validator("personalidad", mode="before")
    @classmethod
    def default_personalidad(cls, v: object) -> str:
        if not v or (isinstance(v, str) and not v.strip()):
            return "amable, profesional y cercano"
        return v

    @field_validator(
        "nombre_bot", "frase_saludo", "frase_des", "frase_no_sabe", "archivo_saludo",
        mode="before",
    )
    @classmethod
    def empty_str_to_none(cls, v: object) -> object:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    model_config = {"extra": "ignore"}


class ChatRequest(BaseModel):
    """Request del gateway al agente de ventas."""

    message: str = Field(..., min_length=1, max_length=4096)
    session_id: int
    id_empresa: int
    config: VentasConfig | None = None


class ChatResponse(BaseModel):
    reply: str
    url: str | None = None
