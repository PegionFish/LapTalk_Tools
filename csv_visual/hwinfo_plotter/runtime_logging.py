from __future__ import annotations

import atexit
import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path


APP_LOGGER_NAME = "csv_visual"
DEFAULT_LOG_FILE_NAME = "csv_visual.log"
DEFAULT_MAX_LOG_BYTES = 2 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 4

_logging_lock = threading.Lock()
_runtime_handler: RotatingFileHandler | None = None
_runtime_log_path: Path | None = None
_hooks_installed = False
_atexit_registered = False


def resolve_default_log_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "logs"

    return Path(__file__).resolve().parents[1] / "logs"


def configure_runtime_logging(
    log_directory: Path | str | None = None,
    *,
    force_reconfigure: bool = False,
) -> Path:
    global _runtime_handler, _runtime_log_path, _atexit_registered
    with _logging_lock:
        if _runtime_handler is not None and _runtime_log_path is not None and not force_reconfigure:
            return _runtime_log_path

        app_logger = logging.getLogger(APP_LOGGER_NAME)
        if _runtime_handler is not None:
            app_logger.removeHandler(_runtime_handler)
            _runtime_handler.close()
            _runtime_handler = None
            _runtime_log_path = None

        resolved_log_directory = (
            Path(log_directory).expanduser().resolve()
            if log_directory is not None
            else resolve_default_log_directory()
        )
        resolved_log_directory.mkdir(parents=True, exist_ok=True)
        log_path = resolved_log_directory / DEFAULT_LOG_FILE_NAME

        handler = RotatingFileHandler(
            log_path,
            maxBytes=DEFAULT_MAX_LOG_BYTES,
            backupCount=DEFAULT_BACKUP_COUNT,
            encoding="utf-8",
        )
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(
            logging.Formatter(
                fmt=(
                    "%(asctime)s.%(msecs)03d "
                    "[%(levelname)s] "
                    "[%(threadName)s] "
                    "[pid=%(process)d] "
                    "%(name)s - %(message)s"
                ),
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )

        app_logger.setLevel(logging.DEBUG)
        app_logger.propagate = False
        app_logger.addHandler(handler)

        _runtime_handler = handler
        _runtime_log_path = log_path
        _install_exception_hooks()
        if not _atexit_registered:
            atexit.register(_log_session_end)
            _atexit_registered = True

        runtime_logger = logging.getLogger(f"{APP_LOGGER_NAME}.runtime")
        runtime_logger.info(
            (
                "=== Session start pid=%s cwd=%s executable=%s "
                "python=%s argv=%s log_path=%s ==="
            ),
            os.getpid(),
            Path.cwd(),
            sys.executable,
            sys.version.split()[0],
            list(sys.argv),
            log_path,
        )
        handler.flush()
        return log_path


def get_runtime_log_path() -> Path | None:
    return _runtime_log_path


def flush_runtime_logging() -> None:
    with _logging_lock:
        if _runtime_handler is not None:
            _runtime_handler.flush()


def shutdown_runtime_logging() -> None:
    global _runtime_handler, _runtime_log_path
    with _logging_lock:
        if _runtime_handler is None:
            return

        runtime_logger = logging.getLogger(f"{APP_LOGGER_NAME}.runtime")
        runtime_logger.info("=== Logging shutdown ===")
        _runtime_handler.flush()

        app_logger = logging.getLogger(APP_LOGGER_NAME)
        app_logger.removeHandler(_runtime_handler)
        _runtime_handler.close()
        _runtime_handler = None
        _runtime_log_path = None


def _log_session_end() -> None:
    with _logging_lock:
        if _runtime_handler is None:
            return

        logging.getLogger(f"{APP_LOGGER_NAME}.runtime").info("=== Session end ===")
        _runtime_handler.flush()


def _install_exception_hooks() -> None:
    global _hooks_installed
    if _hooks_installed:
        return

    previous_sys_excepthook = sys.excepthook

    def runtime_sys_excepthook(exc_type, exc_value, exc_traceback) -> None:
        logging.getLogger(f"{APP_LOGGER_NAME}.runtime").exception(
            "Uncaught exception",
            exc_info=(exc_type, exc_value, exc_traceback),
        )
        previous_sys_excepthook(exc_type, exc_value, exc_traceback)

    sys.excepthook = runtime_sys_excepthook

    if hasattr(threading, "excepthook"):
        previous_threading_excepthook = threading.excepthook

        def runtime_threading_excepthook(args) -> None:
            logging.getLogger(f"{APP_LOGGER_NAME}.runtime").exception(
                "Uncaught thread exception thread=%s",
                args.thread.name if args.thread is not None else "<unknown>",
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )
            previous_threading_excepthook(args)

        threading.excepthook = runtime_threading_excepthook

    _hooks_installed = True


__all__ = [
    "APP_LOGGER_NAME",
    "configure_runtime_logging",
    "flush_runtime_logging",
    "get_runtime_log_path",
    "resolve_default_log_directory",
    "shutdown_runtime_logging",
]
