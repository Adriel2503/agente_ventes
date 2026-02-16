"""
Logging mínimo para el agente de ventas.
"""

import logging
from typing import Optional


def get_logger(name: str) -> logging.Logger:
    """Devuelve un logger con el nombre dado."""
    return logging.getLogger(name)


def setup_logging(
    level: str = "INFO",
    log_file: Optional[str] = None,
) -> None:
    """Configura el logging global. Opcional; main puede llamarlo."""
    lvl = getattr(logging, level.upper(), logging.INFO)
    fmt = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    logging.basicConfig(level=lvl, format=fmt)
    if log_file:
        handler = logging.FileHandler(log_file, encoding="utf-8")
        handler.setFormatter(logging.Formatter(fmt))
        logging.getLogger().addHandler(handler)
