import os
import sys
from loguru import logger

_logging_initialized = False

def setup_logging(log_dir: str = None):
    global _logging_initialized
    if _logging_initialized:
        return

    if not log_dir:
        log_dir = os.environ.get("LOG_DIR")
        if not log_dir:
            if os.path.exists("/app"):
                log_dir = "/app/logs"
            else:
                log_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../logs"))

    try:
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, "pipeline_{time:YYYY-MM-DD}.log")
        logger.add(
            log_file,
            rotation="10 MB",
            retention="30 days",
            level="DEBUG",
            encoding="utf-8",
            backtrace=True,
            diagnose=True,
            enqueue=True,
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {name}:{function}:{line} - {message}"
        )
        _logging_initialized = True
        logger.info(f"Persistent file logging initialized at: {log_file}")
    except Exception as e:
        logger.warning(f"Failed to initialize file logging in {log_dir}: {e}")
