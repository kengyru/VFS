"""
Monitoring service for VFS slots.

Сервис мониторинга в фоне:
- циклические проверки со случайной задержкой
- дедупликация слотов (in-memory + JSON-кэш)
- обработка капчи с паузой
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Awaitable, Callable, Optional, Set

from .browser import CaptchaDetected, VFSBrowser
from .config import BASE_DIR, DATA_DIR, get_settings
from .models import MonitorState, Slot
from .utils import jitter_delay

logger = logging.getLogger(__name__)


SLOT_CACHE_PATH = DATA_DIR / "slot_cache.json"


def _slot_hash(slot: Slot) -> str:
    """Return deterministic hashable representation for a slot."""
    return f"{slot.date.date().isoformat()}|{slot.start_time.isoformat()}|{slot.location}|{slot.service}"


NotifyFunc = Callable[[str], Awaitable[None]]
NotifySlotsFunc = Callable[[str, list[Slot]], Awaitable[None]]
NotifyCaptchaFunc = Callable[[str, Path], Awaitable[None]]


@dataclass
class MonitorService:
    """High-level monitoring loop."""

    on_text: NotifyFunc
    on_slots: NotifySlotsFunc
    on_captcha: NotifyCaptchaFunc
    _state: MonitorState = field(default_factory=MonitorState)
    _task: Optional[asyncio.Task[None]] = None
    _stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    _known_hashes: Set[str] = field(default_factory=set)
    _captcha_until: Optional[datetime] = None
    _consecutive_errors: int = 0

    def __post_init__(self) -> None:
        self._load_cache()

    # region cache
    def _load_cache(self) -> None:
        if not SLOT_CACHE_PATH.exists():
            return
        try:
            data = json.loads(SLOT_CACHE_PATH.read_text(encoding="utf-8"))
            hashes = set(data.get("hashes", []))
            self._known_hashes = hashes
            logger.info("Loaded %s slot hashes from cache", len(hashes))
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to load slot cache: %s", e)

    def _save_cache(self) -> None:
        try:
            SLOT_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            SLOT_CACHE_PATH.write_text(
                json.dumps({"hashes": list(self._known_hashes)}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to save slot cache: %s", e)

    # endregion

    @property
    def is_running(self) -> bool:
        return self._state.is_running

    @property
    def state(self) -> MonitorState:
        return self._state

    async def start(self) -> None:
        if self._task and not self._task.done():
            logger.info("Monitor already running")
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop(), name="vfs-monitor-loop")
        await self.on_text("Мониторинг запущен ✅")

    async def stop(self) -> None:
        if not self._task:
            return
        self._stop_event.set()
        await self.on_text("Останавливаю мониторинг...")
        try:
            await asyncio.wait_for(self._task, timeout=30)
        except asyncio.TimeoutError:
            logger.warning("Monitor task did not stop within timeout")
        self._task = None
        self._state.is_running = False
        await self.on_text("Мониторинг остановлен ⏹️")

    async def _run_loop(self) -> None:
        settings = get_settings()
        self._state.is_running = True
        self._state.last_error = None
        self._consecutive_errors = 0
        browser = VFSBrowser()

        while not self._stop_event.is_set():
            # Базовая задержка между попытками; может быть увеличена при ошибках
            delay = jitter_delay(
                settings.monitor.check_interval,
                settings.monitor.check_interval_variation,
            )
            # Увеличиваем интервал при частых ошибках, чтобы не флудить сайт
            if self._consecutive_errors:
                factor = min(5, 1 + self._consecutive_errors)
                delay *= factor

            # Пауза, если была капча
            if self._captcha_until and datetime.utcnow() < self._captcha_until:
                remaining = (self._captcha_until - datetime.utcnow()).total_seconds()
                logger.warning("Captcha cooldown active for %.0f seconds", remaining)
                await asyncio.sleep(min(delay, remaining))
                continue

            try:
                self._state.checks_count += 1
                self._state.last_check_at = datetime.utcnow()

                ok = await browser.login(
                    email=settings.vfs.email,
                    password=settings.vfs.password,
                )
                if not ok:
                    msg = "Не удалось авторизоваться в VFS. Проверьте логин/пароль."
                    self._state.last_error = msg
                    logger.error(msg)
                    await self.on_text(msg)
                    # При невалидных учётных данных дальнейшие попытки бессмысленны
                    await self.stop()
                    break

                await browser.navigate_to_booking()
                slots = await browser.get_available_slots()
                new_slots = self._filter_new_slots(slots)

                if new_slots:
                    self._state.slots_found_total += len(new_slots)
                    await self.on_slots("Найдены новые слоты:", new_slots)
                    self._save_cache()
                else:
                    logger.info("No new slots on this check")

                self._state.last_error = None
                self._consecutive_errors = 0
            except CaptchaDetected:
                # Обрабатываем отдельно
                logger.warning("Captcha detected, taking screenshot and pausing monitor")
                screenshot_dir = BASE_DIR / "logs"
                screenshot_dir.mkdir(parents=True, exist_ok=True)
                screenshot_path = screenshot_dir / f"captcha_{datetime.utcnow().isoformat().replace(':', '-')}.png"
                try:
                    await browser.screenshot(screenshot_path)
                except Exception as e:  # noqa: BLE001
                    logger.warning("Failed to capture captcha screenshot: %s", e)

                self._captcha_until = datetime.utcnow() + timedelta(minutes=10)
                await self.on_captcha(
                    "Обнаружена капча/Cloudflare. Мониторинг приостановлен на 10 минут. "
                    "Требуется ручное решение в браузере.",
                    screenshot_path,
                )
            except Exception as e:  # noqa: BLE001
                logger.exception("Unexpected error in monitor loop: %s", e)
                self._state.last_error = str(e)
                self._consecutive_errors += 1
                await self.on_text(
                    f"Ошибка мониторинга: {e!r}. "
                    f"Интервал проверок временно увеличен (серия ошибок: {self._consecutive_errors})."
                )

            if self._stop_event.is_set():
                break
            await asyncio.sleep(delay)

        await browser.close()

    def _filter_new_slots(self, slots: list[Slot]) -> list[Slot]:
        """Return only slots whose hash not in cache; update cache in-memory."""
        new: list[Slot] = []
        for slot in slots:
            h = _slot_hash(slot)
            if h in self._known_hashes:
                continue
            self._known_hashes.add(h)
            new.append(slot)
        return new

