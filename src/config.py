"""
Config loading via Pydantic v2 and python-dotenv.

Загрузка конфигурации из .env и базовая валидация.
"""

from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError, computed_field


BASE_DIR = Path(__file__).resolve().parent.parent
# В Docker можно задать DATA_DIR=/app/data и смонтировать volume — кэш и куки сохранятся
DATA_DIR = Path(os.environ.get("DATA_DIR", str(BASE_DIR)))
ENV_PATH = BASE_DIR / ".env"

# Явно загружаем переменные окружения из .env, если файл существует
if ENV_PATH.exists():
    load_dotenv(ENV_PATH, override=False)


class BotConfig(BaseModel):
    # Значения мы уже читаем из окружения вручную, поэтому alias не нужны
    token: str
    admin_chat_id: int


class VFSConfig(BaseModel):
    email: str
    password: str


class MonitorConfig(BaseModel):
    check_interval: int = Field(ge=30)
    check_interval_variation: int = Field(ge=0)
    target_month: int = Field(ge=1, le=12)
    target_days: Optional[List[int]] = Field(
        default=None,
        description="Список целевых дней месяца. Пусто — любые дни месяца.",
    )
    target_days_of_week: Optional[List[int]] = Field(
        default=None,
        description="Список целевых дней недели (1=Mon..7=Sun). Пусто — любые.",
    )
    target_time_start: str
    target_time_end: str

    @computed_field  # type: ignore[misc]
    @property
    def target_time_range(self) -> tuple[str, str]:
        return self.target_time_start, self.target_time_end


class LoggingConfig(BaseModel):
    logs_dir: Path = Field(default_factory=lambda: BASE_DIR / "logs")
    log_level: str = Field(default="INFO")
    max_bytes: int = Field(default=5 * 1024 * 1024)  # 5 MB
    backup_count: int = Field(default=5)


class Settings(BaseModel):
    bot: BotConfig
    vfs: VFSConfig
    monitor: MonitorConfig
    logging: LoggingConfig = LoggingConfig()


@lru_cache
def get_settings() -> Settings:
    """
    Load and cache settings.

    Raises ValidationError if .env is incomplete or invalid.
    """
    # Собираем значения из окружения вручную, чтобы не зависеть от pydantic-settings
    env = os.environ

    def _split_int_list(value: str | None) -> Optional[List[int]]:
        if not value:
            return None
        return [int(x.strip()) for x in value.split(",") if x.strip()]

    try:
        bot = BotConfig(
            token=env.get("BOT_TOKEN", ""),
            admin_chat_id=int(env.get("ADMIN_CHAT_ID", "0") or "0"),
        )
        vfs = VFSConfig(
            email=env.get("VFS_EMAIL", ""),
            password=env.get("VFS_PASSWORD", ""),
        )
        monitor = MonitorConfig(
            check_interval=int(env.get("CHECK_INTERVAL", "120")),
            check_interval_variation=int(env.get("CHECK_INTERVAL_VARIATION", "30")),
            target_month=int(env.get("TARGET_MONTH", "3")),
            target_days=_split_int_list(env.get("TARGET_DAYS")),
            target_days_of_week=_split_int_list(env.get("TARGET_DAYS_OF_WEEK")),
            target_time_start=env.get("TARGET_TIME_START", "07:00"),
            target_time_end=env.get("TARGET_TIME_END", "22:00"),
        )
        logging_cfg = LoggingConfig()
        return Settings(bot=bot, vfs=vfs, monitor=monitor, logging=logging_cfg)
    except ValidationError:
        # Пробрасываем дальше, чтобы верхний уровень мог вывести аккуратную ошибку
        raise


__all__ = ["Settings", "get_settings", "BASE_DIR", "DATA_DIR"]

