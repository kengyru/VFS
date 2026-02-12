"""
Playwright-based browser automation for VFS Global.

Browser-модуль на Playwright, эмулирующий человеческое поведение:
- случайные движения мыши перед кликом
- рандомные задержки между действиями
- скролл страницы до целевого элемента

ВАЖНО: конкретные селекторы VFS нужно будет уточнить под фактическую разметку.
"""

from __future__ import annotations

import asyncio
import os
import logging
import random
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import AsyncIterator, List, Optional

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from .config import BASE_DIR, DATA_DIR, get_settings
from .models import Slot
from .utils import async_retry

logger = logging.getLogger(__name__)


STORAGE_STATE_PATH = DATA_DIR / "storage_state.json"


@dataclass
class CaptchaDetected(Exception):
    """Raised when Cloudflare / captcha is detected."""

    message: str = "Captcha or Cloudflare protection detected"


class VFSBrowser:
    """
    High-level wrapper around Playwright to work with VFS Global.
    """

    def __init__(self) -> None:
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._startup_ts: Optional[datetime] = None

    @property
    def page(self) -> Page:
        if not self._page:
            raise RuntimeError("Browser not initialised")
        return self._page

    async def _ensure_browser(self) -> None:
        """
        Ensure browser and context are created.

        Ограничиваем время жизни браузера ~1 час: при превышении пересоздаём.
        """
        now = datetime.utcnow()
        if self._browser and self._startup_ts:
            lifetime = (now - self._startup_ts).total_seconds()
            if lifetime > 3600:  # 1 hour
                logger.info("Restarting browser after %s seconds", lifetime)
                await self.close()

        if self._browser:
            return

        logger.info("Starting Playwright browser")
        # PWDEBUG=1 включает headed режим — в контейнере принудительно выключаем
        if "PWDEBUG" in os.environ:
            os.environ.pop("PWDEBUG", None)
        self._playwright = await async_playwright().start()
        # В Docker/VPS всегда headless — нет X server
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        storage_state = STORAGE_STATE_PATH if STORAGE_STATE_PATH.exists() else None
        self._context = await self._browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            storage_state=storage_state,
        )
        self._page = await self._context.new_page()
        self._startup_ts = now

    async def close(self) -> None:
        """Close browser and Playwright."""
        logger.info("Closing Playwright browser")
        try:
            if self._context:
                await self._context.storage_state(path=str(STORAGE_STATE_PATH))
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to save storage_state: %s", e)

        if self._page:
            await self._page.close()
        if self._context:
            await self._context.close()
        if getattr(self, "_browser", None):
            await self._browser.close()  # type: ignore[func-returns-value]
        if getattr(self, "_playwright", None):
            await self._playwright.stop()

        self._browser = None
        self._context = None
        self._page = None
        self._startup_ts = None

    async def _human_delay(self, min_delay: float = 0.5, max_delay: float = 2.0) -> None:
        """Random small delay to mimic human behaviour."""
        await asyncio.sleep(random.uniform(min_delay, max_delay))

    async def _move_mouse_to_element(self, selector: str) -> None:
        """Move mouse in a few steps to the element before clicking."""
        page = self.page
        element = await page.wait_for_selector(selector, timeout=15000)
        box = await element.bounding_box()
        if not box:
            return

        # start from random point on screen
        start_x = random.uniform(0, box["x"] + box["width"] / 2)
        start_y = random.uniform(0, box["y"] + box["height"] / 2)
        await page.mouse.move(start_x, start_y)

        steps = random.randint(5, 12)
        for step in range(steps):
            t = (step + 1) / steps
            x = start_x + (box["x"] + box["width"] / 2 - start_x) * t
            y = start_y + (box["y"] + box["height"] / 2 - start_y) * t
            await page.mouse.move(x, y)
            await asyncio.sleep(random.uniform(0.01, 0.05))

    async def _scroll_into_view(self, selector: str) -> None:
        """Scroll page so element is visible."""
        page = self.page
        element = await page.wait_for_selector(selector, timeout=15000)
        await element.scroll_into_view_if_needed()
        # небольшое доп. смещение
        await page.mouse.wheel(0, random.randint(100, 400))
        await self._human_delay()

    async def _human_click(self, selector: str) -> None:
        """Scroll, move mouse and click element."""
        await self._scroll_into_view(selector)
        await self._move_mouse_to_element(selector)
        await self._human_delay()
        await self.page.click(selector, delay=random.randint(50, 150))
        await self._human_delay()

    async def _check_captcha(self) -> None:
        """
        Try to detect Cloudflare / captcha presence.

        Т.к. точная разметка неизвестна, проверяем несколько типичных признаков.
        """
        page = self.page
        text_candidates = [
            "captcha",
            "Cloudflare",
            "verify you are human",
            "подтвердите, что вы не робот",
        ]
        body_text = (await page.text_content("body")) or ""
        lower = body_text.lower()
        if any(token.lower() in lower for token in text_candidates):
            logger.warning("Captcha / Cloudflare detected on page")
            raise CaptchaDetected()

    @async_retry(attempts=3, base_delay=3, max_delay=60)
    async def login(self, *, email: str, password: str) -> bool:
        """
        Perform login to VFS Global. Returns True on success.

        ВАЖНО: адаптируйте URL и селекторы под актуальный сайт VFS.
        """
        await self._ensure_browser()
        page = self.page

        # Сначала главная страница — иногда помогает пройти Cloudflare
        base_url = "https://visa.vfsglobal.com/rus/ru/bgr"
        logger.info("Opening main page %s", base_url)
        await page.goto(base_url, wait_until="domcontentloaded", timeout=60000)
        await self._human_delay()
        await self._check_captcha()

        # Потом форма логина
        login_url = f"{base_url}/login"
        logger.info("Opening login page %s", login_url)
        await page.goto(login_url, wait_until="domcontentloaded", timeout=60000)
        await self._human_delay()
        await self._check_captcha()

        # Пробуем разные селекторы поля email (разметка VFS может отличаться)
        email_selectors = [
            'input[name="email"]',
            'input[type="email"]',
            'input[name="username"]',
            'input[id="email"]',
            'input[id="Email"]',
            'input[placeholder*="mail"]',
            'input[placeholder*="почт"]',
        ]
        email_filled = False
        for sel in email_selectors:
            try:
                el = await page.wait_for_selector(sel, timeout=3000)
                if el:
                    await self._scroll_into_view(sel)
                    await page.fill(sel, email)
                    email_filled = True
                    break
            except Exception:  # noqa: BLE001
                continue
        if not email_filled:
            raise RuntimeError("Не найдено поле email на странице логина")

        await self._human_delay()

        # Поле пароля
        pwd_selectors = [
            'input[name="password"]',
            'input[type="password"]',
        ]
        pwd_filled = False
        for sel in pwd_selectors:
            try:
                el = await page.wait_for_selector(sel, timeout=3000)
                if el:
                    await self._scroll_into_view(sel)
                    await page.fill(sel, password)
                    pwd_filled = True
                    break
            except Exception:  # noqa: BLE001
                continue
        if not pwd_filled:
            raise RuntimeError("Не найдено поле пароля на странице логина")

        await self._human_delay()

        await self._human_click('button[type="submit"]')

        # Ждём появления дашборда/ссылки "Мои заявки" и т.п.
        try:
            await page.wait_for_selector('text="Мои заявки"', timeout=30000)
            await self._check_captcha()
        except Exception as e:  # noqa: BLE001
            logger.warning("Login likely failed: %s", e)
            await self._check_captcha()
            return False

        # Сохраняем состояние для последующих сессий
        if self._context:
            try:
                STORAGE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
                await self._context.storage_state(path=str(STORAGE_STATE_PATH))
            except Exception as e:  # noqa: BLE001
                logger.warning("Failed to persist storage_state: %s", e)

        logger.info("Login succeeded")
        return True

    @asynccontextmanager
    async def session(self) -> AsyncIterator["VFSBrowser"]:
        """
        Async context manager for using browser.

        Пример:
            async with VFSBrowser().session() as vfs:
                await vfs.login(...)
        """
        try:
            await self._ensure_browser()
            yield self
        finally:
            await self.close()

    @async_retry(attempts=3, base_delay=3, max_delay=60)
    async def navigate_to_booking(self) -> None:
        """
        Navigate to Bulgaria → Moscow → specific service booking page.

        Здесь используются примерные селекторы, их нужно будет
        донастроить под реальный UI VFS.
        """
        page = self.page
        logger.info("Navigating to booking page")

        # Примерный сценарий: выбор страны, города и услуги.
        # TODO: заменить селекторы на актуальные.
        await self._check_captcha()
        await self._human_click('text="Болгария"')
        await self._human_click('text="Москва"')
        await self._human_click('text="Запись на подачу документов"')

        # Переход к календарю бронирования
        await self._human_click('text="Записаться на приём"')
        await self._check_captcha()

    @async_retry(attempts=3, base_delay=5, max_delay=90)
    async def get_available_slots(self) -> List[Slot]:
        """
        Parse booking calendar and return available slots.

        Возвращает список объектов Slot, отфильтрованных по конфигу.
        """
        await self._check_captcha()

        settings = get_settings()
        target_month = settings.monitor.target_month
        time_start_str, time_end_str = settings.monitor.target_time_range
        time_start = time.fromisoformat(time_start_str)
        time_end = time.fromisoformat(time_end_str)

        # ВАЖНО: ниже псевдокод, т.к. структура календаря неизвестна.
        # Предположим, что есть элементы ячеек календаря с data-date и
        # дочерними слотами с временем.

        page = self.page
        logger.info("Parsing available slots")

        slots: List[Slot] = []
        calendar_cells = await page.query_selector_all('[data-testid="calendar-cell"]')
        for cell in calendar_cells:
            date_attr = await cell.get_attribute("data-date")
            if not date_attr:
                continue
            try:
                # пример формата "2026-03-15"
                cell_date = datetime.fromisoformat(date_attr)
            except ValueError:
                continue

            if cell_date.month != target_month:
                continue

            # фильтр по дням
            md_cfg = settings.monitor
            if md_cfg.target_days and cell_date.day not in md_cfg.target_days:
                continue
            if md_cfg.target_days_of_week and cell_date.isoweekday() not in md_cfg.target_days_of_week:
                continue

            # Слоты времени внутри ячейки
            time_elements = await cell.query_selector_all('[data-testid="slot-time"]')
            for t_el in time_elements:
                time_text = (await t_el.text_content()) or ""
                time_text = time_text.strip()
                try:
                    start_t = time.fromisoformat(time_text)
                except ValueError:
                    continue

                if not (time_start <= start_t <= time_end):
                    continue

                slot = Slot(
                    date=cell_date,
                    start_time=start_t,
                    end_time=None,
                    location="Москва VFS",  # можно параметризовать
                    service="Виза Болгарии",  # можно параметризовать
                    notes=None,
                )
                slots.append(slot)

        logger.info("Found %s matching slots", len(slots))
        return slots

    async def screenshot(self, path: Path) -> Path:
        """Capture screenshot of current page."""
        await self.page.screenshot(path=str(path), full_page=True)
        return path

