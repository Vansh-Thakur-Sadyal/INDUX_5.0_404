# utils/logger.py

import logging
import os


def get_logger(name: str = "smart_farming") -> logging.Logger:
    """
    Returns a configured logger instance.

    Features:
    - Console logging
    - File logging (optional)
    - Consistent format across the pipeline

    Args:
        name (str): Logger name

    Returns:
        logging.Logger
    """

    logger = logging.getLogger(name)

    # Prevent duplicate handlers
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)

    # -----------------------------------
    # FORMATTER
    # -----------------------------------
    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] %(name)s: %(message)s"
    )

    # -----------------------------------
    # CONSOLE HANDLER
    # -----------------------------------
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # -----------------------------------
    # FILE HANDLER (optional)
    # -----------------------------------
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)

    file_handler = logging.FileHandler(
        os.path.join(log_dir, "pipeline.log"), encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger
