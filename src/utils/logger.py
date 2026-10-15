"""Simple rich logger for Oxaniq."""

from __future__ import annotations
import logging
from rich.console import Console
from rich.logging import RichHandler

console = Console()

def setup_logger(level: str = "INFO") -> logging.Logger:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True, markup=True)],
    )
    logger = logging.getLogger("oxaniq")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    return logger
