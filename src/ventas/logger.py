"""
Sistema de logging centralizado para el agente de ventas.
Configura logging consistente en toda la aplicación.
"""

import logging
import sys
from pathlib import Path

# Niveles de log
DEBUG = logging.DEBUG
INFO = logging.INFO
WARNING = logging.WARNING
ERROR = logging.ERROR
CRITICAL = logging.CRITICAL


def setup_logging(
    level: int = logging.INFO,
    log_file: str | None = None,
    log_format: str | None = None,
) -> None:
    """
    Configura el sistema de logging para toda la aplicación.

    Args:
        level: Nivel de logging (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Ruta al archivo de log (opcional)
        log_format: Formato personalizado de log (opcional)
    """
    if log_format is None:
        log_format = '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'

    handlers = [logging.StreamHandler(sys.stdout)]

    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding='utf-8'))

    logging.basicConfig(
        level=level,
        format=log_format,
        handlers=handlers,
        force=True  # Sobreescribir configuración existente
    )

    # Silenciar loggers ruidosos de terceros
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("langchain").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """
    Obtiene un logger con el nombre especificado.

    Args:
        name: Nombre del logger (usualmente __name__ del módulo)

    Returns:
        Logger configurado

    Example:
        >>> logger = get_logger(__name__)
        >>> logger.info("Mensaje de log")
    """
    return logging.getLogger(name)


# Logger por defecto para uso rápido
logger = get_logger("ventas")

__all__ = ["setup_logging", "get_logger", "logger", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
