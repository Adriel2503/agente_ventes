"""
Configuración del agente de ventas (env, API, OpenAI, servidor).
Incluye validación de tipos/valores y anotaciones para IDE y documentación.
"""

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


def _find_env_path() -> Path:
    """Busca .env hacia arriba desde el módulo actual."""
    current = Path(__file__).resolve().parent
    for _ in range(6):
        env_file = current / ".env"
        if env_file.exists():
            return env_file
        parent = current.parent
        if parent == current:
            break
        current = parent
    return Path.cwd() / ".env"


load_dotenv(_find_env_path())


# ---------------------------------------------------------------------------
# Helpers de lectura con validación
# ---------------------------------------------------------------------------


def _get_str(key: str, default: str) -> str:
    """Obtiene variable de entorno como string."""
    value = os.getenv(key, default)
    return value.strip() if isinstance(value, str) else str(default)


def _get_int(
    key: str,
    default: int,
    min_val: Optional[int] = None,
    max_val: Optional[int] = None,
) -> int:
    """Obtiene variable de entorno como int; valida y usa default si es inválida."""
    raw = os.getenv(key, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    if min_val is not None and value < min_val:
        return default
    if max_val is not None and value > max_val:
        return default
    return value


def _get_float(
    key: str,
    default: float,
    min_val: Optional[float] = None,
    max_val: Optional[float] = None,
) -> float:
    """Obtiene variable de entorno como float; valida y usa default si es inválida."""
    raw = os.getenv(key, str(default))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if min_val is not None and value < min_val:
        return default
    if max_val is not None and value > max_val:
        return default
    return value


def _get_log_level(key: str, default: str) -> str:
    """Obtiene nivel de log; si no es válido, retorna default."""
    value = (os.getenv(key) or default).strip().upper()
    if value in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        return value
    return default.upper()


# ---------------------------------------------------------------------------
# OpenAI (agente especializado en ventas)
# ---------------------------------------------------------------------------

OPENAI_API_KEY: str = _get_str("OPENAI_API_KEY", "")
OPENAI_MODEL: str = _get_str("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_TEMPERATURE: float = _get_float("OPENAI_TEMPERATURE", 0.5, min_val=0.0, max_val=2.0)
OPENAI_TIMEOUT: int = _get_int("OPENAI_TIMEOUT", 90, min_val=1, max_val=300)
MAX_TOKENS: int = _get_int("MAX_TOKENS", 2048, min_val=1, max_val=128000)


# ---------------------------------------------------------------------------
# Configuración del servidor
# ---------------------------------------------------------------------------

SERVER_HOST: str = _get_str("SERVER_HOST", "0.0.0.0")
SERVER_PORT: int = _get_int("SERVER_PORT", 8001, min_val=1, max_val=65535)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_LEVEL: str = _get_log_level("LOG_LEVEL", "INFO")
LOG_FILE: str = _get_str("LOG_FILE", "")  # Si está vacío, no guarda en archivo


# ---------------------------------------------------------------------------
# Timeouts y límites
# ---------------------------------------------------------------------------

# API_TIMEOUT: por cada request HTTP (categorías, sucursales, búsqueda).
# CHAT_TIMEOUT: global por mensaje en main; debe ser >= OPENAI_TIMEOUT.
API_TIMEOUT: int = _get_int("API_TIMEOUT", 10, min_val=1, max_val=120)
CHAT_TIMEOUT: int = _get_int("CHAT_TIMEOUT", 120, min_val=30, max_val=300)


# ---------------------------------------------------------------------------
# API MaravIA (productos, categorías, sucursales)
# ---------------------------------------------------------------------------

API_INFORMACION_URL: str = _get_str(
    "API_INFORMACION_URL",
    "https://api.maravia.pe/servicio/ws_informacion_ia.php",
)
API_PREGUNTAS_FRECUENTES_URL: str = _get_str(
    "API_PREGUNTAS_FRECUENTES_URL",
    "https://api.maravia.pe/servicio/n8n/ws_preguntas_frecuentes.php",
)
