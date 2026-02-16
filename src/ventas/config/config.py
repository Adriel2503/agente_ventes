"""
Configuración del agente de ventas (env, API, OpenAI, servidor).
"""

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

_BASE_DIR: Path = Path(__file__).resolve().parent.parent.parent.parent
load_dotenv(_BASE_DIR / ".env")


def _get_str(key: str, default: str) -> str:
    value = os.getenv(key, default)
    return value.strip() if isinstance(value, str) else str(default)


def _get_int(key: str, default: int, min_val: Optional[int] = None, max_val: Optional[int] = None) -> int:
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


def _get_float(key: str, default: float, min_val: Optional[float] = None, max_val: Optional[float] = None) -> float:
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


# API MaravIA (ws_informacion_ia.php)
API_INFORMACION_URL: str = _get_str(
    "API_INFORMACION_URL",
    "https://api.maravia.pe/servicio/ws_informacion_ia.php",
)
API_TIMEOUT: int = _get_int("API_TIMEOUT", 10, min_val=1, max_val=120)
ID_EMPRESA: int = _get_int("ID_EMPRESA", 1, min_val=1)

# OpenAI (agente)
OPENAI_API_KEY: str = _get_str("OPENAI_API_KEY", "")
OPENAI_MODEL: str = _get_str("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_TEMPERATURE: float = _get_float("OPENAI_TEMPERATURE", 0.5, min_val=0.0, max_val=2.0)
OPENAI_TIMEOUT: int = _get_int("OPENAI_TIMEOUT", 90, min_val=1, max_val=300)
MAX_TOKENS: int = _get_int("MAX_TOKENS", 2048, min_val=1, max_val=128000)

# Servidor MCP
SERVER_HOST: str = _get_str("SERVER_HOST", "0.0.0.0")
SERVER_PORT: int = _get_int("SERVER_PORT", 8001, min_val=1, max_val=65535)

# Logging
LOG_LEVEL: str = _get_str("LOG_LEVEL", "INFO").upper()
if LOG_LEVEL not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
    LOG_LEVEL = "INFO"
