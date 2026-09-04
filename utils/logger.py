"""
Logging configuration for Rosetta v2.

Provides structured logging with file and console handlers.
"""

import logging
import sys
from pathlib import Path
from typing import Optional


def setup_logger(
    name: str,
    level: Optional[str] = None,
    log_file: Optional[Path] = None
) -> logging.Logger:
    """
    Set up a logger with consistent formatting.
    
    Args:
        name: Name of the logger (typically __name__ of the module)
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Optional file path to write logs to
        
    Returns:
        Configured logger instance
    """
    # Create logger
    logger = logging.getLogger(name)
    
    # Set level
    if level is None:
        try:
            from config import Config
            level = Config.LOG_LEVEL
        except ImportError:
            level = "INFO"
    
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    
    # Avoid adding handlers multiple times
    if logger.handlers:
        return logger
    
    # Create formatters
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # File handler (optional)
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    
    return logger


def get_logger(name: str) -> logging.Logger:
    """
    Get or create a logger for the specified module.
    
    Args:
        name: Name of the logger (typically __name__ of the module)
        
    Returns:
        Logger instance
    """
    # Check if logger already exists
    logger = logging.getLogger(name)
    
    # If not configured yet, set it up
    if not logger.handlers:
        # Try to get log file path from config
        log_file = None
        try:
            from config import Config
            log_file = Config.LOGS_DIR / "rosetta.log"
        except ImportError:
            pass
        
        return setup_logger(name, log_file=log_file)
    
    
    return logger


def configure_logging(verbose: bool = False, log_file: Optional[Path] = None):
    """
    Configure global logging settings.
    
    Args:
        verbose: If True, set level to DEBUG, else INFO
        log_file: Optional path to log file
    """
    level = "DEBUG" if verbose else "INFO"
    
    # Configure root logger
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True
    )
    
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        file_handler.setFormatter(formatter)
        logging.getLogger().addHandler(file_handler)

