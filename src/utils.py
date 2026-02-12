"""
Utility helpers: logging setup, retry decorators, small helpers.

Вспомогательные функции: настройка логирования, ретраи и т.п.
"""

from __future__ import annotations

import asyncio
import logging
import random
from functools import wraps
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Awaitable, Callable, TypeVar

from .config import LoggingConfig, get_settings


T = TypeVar("T")


def setup_logging(logging_cfg: LoggingConfig | None = None) -> None:
    """
    Configure application-wide logging with rotation.

    Настраивает логирование в файл с ротацией и вывод в консоль.
    """
    if logging_cfg is None:
        logging_cfg = get_settings().logging

    logs_dir: Path = logging_cfg.logs_dir
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / "vfs_bot.log"

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=logging_cfg.max_bytes,
        backupCount=logging_cfg.backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging_cfg.log_level.upper())
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(console_handler)


def jitter_delay(base_seconds: int, variation_seconds: int) -> float:
    """
    Calculate delay with random +/- variation.

    Возвращает задержку в секундах с рандомным отклонением.
    """
    if variation_seconds <= 0:
        return float(base_seconds)
    delta = random.uniform(-variation_seconds, variation_seconds)
    delay = max(1.0, base_seconds + delta)
    return delay


def async_retry(
    attempts: int = 3,
    base_delay: float = 2.0,
    max_delay: float = 60.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """
    Simple exponential backoff retry decorator for async functions.

    Простой декоратор ретраев с экспоненциальной задержкой.
    """

    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            attempt = 0
            delay = base_delay
            while True:
                try:
                    return await func(*args, **kwargs)
                except exceptions as exc:  # type: ignore[misc]
                    attempt += 1
                    if attempt >= attempts:
                        raise
                    logging.getLogger(func.__module__).warning(
                        "Retrying %s after error %s (attempt %s/%s, delay %.1fs)",
                        func.__name__,
                        exc,
                        attempt,
                        attempts,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    delay = min(max_delay, delay * 2)

        return wrapper

    return decorator


__all__ = ["setup_logging", "jitter_delay", "async_retry"]

