"""
Schema de respuesta estructurada y parsing de contenido multimodal.
"""

import re

from pydantic import BaseModel


class VentasStructuredResponse(BaseModel):
    """Schema para response_format. reply obligatorio; url opcional (ej. video/imagen de saludo)."""

    reply: str
    url: str | None = None


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
