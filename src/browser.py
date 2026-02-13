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
import subprocess
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import AsyncIterator, List, Optional
from urllib.parse import urlparse

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from .config import BASE_DIR, DATA_DIR, get_settings
from .models import Slot
from .utils import async_retry

logger = logging.getLogger(__name__)


STORAGE_STATE_PATH = DATA_DIR / "storage_state.json"

# Порты по умолчанию для CDP
DEFAULT_CDP_PORT = 9222


def _find_chrome_executable() -> Optional[str]:
    """Ищем chrome.exe на Windows. Сначала проверяем CHROME_PATH из .env."""
    if sys.platform != "win32":
        return None
    env_path = os.environ.get("CHROME_PATH", "").strip()
    if env_path:
        p = Path(env_path)
        if p.is_file() and p.exists():
            return str(p)
        # если указана папка Application
        if p.is_dir():
            exe = p / "chrome.exe"
            if exe.exists():
                return str(exe)
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        Path(r"D:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    ]
    for p in candidates:
        if p and p.exists():
            return str(p)
    return None


def _launch_chrome_with_cdp(port: int) -> bool:
    """
    Запускаем Chrome с --remote-debugging-port=port.
    Используем отдельный --user-data-dir, чтобы получился новый процесс (порт точно откроется),
    даже если обычный Chrome уже запущен.
    """
    chrome = _find_chrome_executable()
    if not chrome:
        logger.warning("Chrome не найден в стандартных путях, автозапуск пропущен")
        return False
    user_data_dir = BASE_DIR / "data" / "chrome_cdp_profile"
    user_data_dir.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.Popen(
            [
                chrome,
                f"--remote-debugging-port={port}",
                f"--user-data-dir={user_data_dir}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("Запущен Chrome с отладкой на порту %s", port)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("Не удалось запустить Chrome: %s", e)
        return False


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
        self._cdp_mode: bool = False  # True = подключены к уже запущенному Chrome

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

        cdp_url = os.environ.get("CHROME_CDP_URL", "").strip()

        if cdp_url:
            # Подключаемся к Chrome по CDP. Опционально бот сам запускает Chrome (CHROME_LAUNCH_CDP=1).
            launch_cdp_val = (
                os.environ.get("CHROME_LAUNCH_CDP") or os.environ.get(" CHROME_LAUNCH_CDP") or ""
            )
            launch_cdp = launch_cdp_val.strip().lower() in ("1", "true", "yes")
            if launch_cdp:
                parsed = urlparse(cdp_url)
                port = parsed.port or DEFAULT_CDP_PORT
                if _launch_chrome_with_cdp(port):
                    await asyncio.sleep(5)

            logger.info("Connecting to Chrome at %s", cdp_url)
            if "PWDEBUG" in os.environ:
                os.environ.pop("PWDEBUG", None)
            self._playwright = await async_playwright().start()
            last_error: Optional[Exception] = None
            for attempt in range(3):
                try:
                    self._browser = await self._playwright.chromium.connect_over_cdp(cdp_url)
                    break
                except Exception as e:  # noqa: BLE001
                    last_error = e
                    if attempt < 2:
                        await asyncio.sleep(2)
            else:
                hint = (
                    " В .env задайте CHROME_LAUNCH_CDP=1 — бот сам запустит Chrome. "
                    "Либо закройте весь Chrome и запустите вручную: chrome.exe --remote-debugging-port=9222"
                )
                raise RuntimeError(f"Не удалось подключиться к Chrome по {cdp_url}.{hint}") from last_error
            self._cdp_mode = True

            contexts = self._browser.contexts
            if contexts:
                self._context = contexts[0]
                self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
            else:
                self._context = await self._browser.new_context()
                self._page = await self._context.new_page()
        else:
            # Классический запуск через Playwright (часто режется антиботом VFS).
            logger.info("Starting Playwright browser")
            if "PWDEBUG" in os.environ:
                os.environ.pop("PWDEBUG", None)
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                channel="chrome",
                headless=False,
            )
            self._cdp_mode = False

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
        """Close browser and Playwright. В режиме CDP только отключаемся, окно Chrome не закрываем."""
        logger.info("Closing Playwright browser" + (" (disconnect)" if getattr(self, "_cdp_mode", False) else ""))
        if not getattr(self, "_cdp_mode", False):
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

    async def _try_click(self, selector: str, timeout_ms: int = 4000) -> bool:
        """Попытка кликнуть по селектору с коротким таймаутом. Возвращает True при успехе."""
        try:
            el = await self.page.wait_for_selector(selector, timeout=timeout_ms)
            if el:
                await el.scroll_into_view_if_needed()
                await self.page.click(selector, timeout=timeout_ms)
                return True
        except Exception:  # noqa: BLE001
            pass
        return False

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
        В ручном режиме (VFS_MANUAL_LOGIN=1) бот логин не выполняет, а
        лишь подключается к уже авторизованному браузеру.
        """
        manual_login = os.environ.get("VFS_MANUAL_LOGIN", "").strip().lower() in ("1", "true", "yes")

        await self._ensure_browser()
        page = self.page

        if manual_login:
            # Ручной режим: предполагаем, что пользователь уже залогинился в этом браузере.
            base_url = "https://visa.vfsglobal.com/rus/ru/bgr"
            logger.info("Manual login mode enabled – assuming existing session, opening %s", base_url)
            resp = await page.goto(base_url, wait_until="domcontentloaded", timeout=60000)
            if resp and resp.status >= 400:
                logger.error("Manual login mode: country page HTTP %s", resp.status)
                return False
            await self._human_delay()
            try:
                await self._check_captcha()
            except CaptchaDetected:
                return False
            return True

        # ШАГ 1. В режиме CDP сразу открываем страницу страны (корень часто 403).
        # Без CDP — пробуем корень, при 403 переходим на страницу страны.
        base_url = "https://visa.vfsglobal.com/rus/ru/bgr"
        root_url = "https://visa.vfsglobal.com/"
        skip_country_selector = False

        if getattr(self, "_cdp_mode", False):
            logger.info("Opening country page %s (CDP)", base_url)
            resp = await page.goto(base_url, wait_until="domcontentloaded", timeout=60000)
            if resp and resp.status >= 400:
                logger.error("Country page returned HTTP %s", resp.status)
                raise RuntimeError(
                    f"VFS country page HTTP error {resp.status}. "
                    "Сайт блокирует сессию. Уберите CHROME_LAUNCH_CDP из .env и вручную запустите Chrome с --remote-debugging-port=9222 (ваш профиль)."
                )
            skip_country_selector = True
        else:
            logger.info("Opening root page %s", root_url)
            resp = await page.goto(root_url, wait_until="domcontentloaded", timeout=60000)
            if resp and resp.status >= 400:
                logger.warning("Root page HTTP %s, trying country page %s", resp.status, base_url)
                resp2 = await page.goto(base_url, wait_until="domcontentloaded", timeout=60000)
                if resp2 and resp2.status >= 400:
                    logger.error("Country page returned HTTP %s", resp2.status)
                    raise RuntimeError(
                        f"VFS country page HTTP error {resp2.status}. "
                        "Сайт блокирует эту сессию (403201). Попробуйте: убрать CHROME_LAUNCH_CDP из .env, "
                        "вручную запустить свой Chrome с флагом --remote-debugging-port=9222 — бот подключится к нему с вашим профилем и куки."
                    )
                skip_country_selector = True
        await self._human_delay()
        await self._check_captcha()

        # Ожидаем появления формы выбора стран (если зашли с корня). При 403 мы уже на base_url — пропускаем.
        if not skip_country_selector:
            try:
                # Первый селект — "I am a resident of"
                resident_selectors = [
                    'select[formcontrolname="residentCountry"]',
                    'select[name*="resident" i]',
                    'select:has(option:text("Select Your Country"))',
                    'select[placeholder*="resident" i]',
                ]
                going_selectors = [
                    'select[formcontrolname="destinationCountry"]',
                    'select[name*="destination" i]',
                    'select:has(option:text("Select Country"))',
                    'select[placeholder*="Going to" i]',
                ]

                resident_sel = None
                for sel in resident_selectors:
                    try:
                        el = await page.wait_for_selector(sel, timeout=5000)
                        if el:
                            resident_sel = sel
                            break
                    except Exception:  # noqa: BLE001
                        continue

                going_sel = None
                for sel in going_selectors:
                    try:
                        el = await page.wait_for_selector(sel, timeout=5000)
                        if el:
                            going_sel = sel
                            break
                    except Exception:  # noqa: BLE001
                        continue

                if not resident_sel or not going_sel:
                    raise RuntimeError("Не найдены селекты выбора стран на root-странице VFS")

                # Выбираем "Russia" и "Bulgaria" по видимому тексту.
                await page.select_option(resident_sel, label="Russia")
                await self._human_delay()
                await page.select_option(going_sel, label="Bulgaria")
                await self._human_delay()

                # Кнопка Confirm
                confirm_selectors = [
                    'button:has-text("Confirm")',
                    'button[type="submit"]',
                ]
                confirm_clicked = False
                for sel in confirm_selectors:
                    try:
                        await self._human_click(sel)
                        confirm_clicked = True
                        break
                    except Exception:  # noqa: BLE001
                        continue
                if not confirm_clicked:
                    raise RuntimeError("Не найдена кнопка Confirm на селекторе стран VFS")

                # Ждём перехода на страницу конкретной страны/города.
                await page.wait_for_load_state("domcontentloaded", timeout=60000)
            except Exception as e:  # noqa: BLE001
                # Сохраняем скриншот для отладки селектора стран.
                try:
                    debug_dir = BASE_DIR / "logs"
                    debug_dir.mkdir(parents=True, exist_ok=True)
                    path = debug_dir / f"country_selector_error_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.png"
                    await page.screenshot(path=str(path), full_page=True)
                    logger.warning("Ошибка при выборе стран, скриншот: %s", path)
                except Exception as se:  # noqa: BLE001
                    logger.warning("Не удалось сохранить скриншот селектора стран: %s", se)
                raise

        await self._human_delay()
        await self._check_captcha()

        # Переход к логину: ищем «Вход» / «Войти» (часто в гамбургер-меню)
        logger.info("Looking for login button...")
        login_clicked = False

        # Сначала пробуем открыть меню (три полоски) — на VFS «Вход» часто внутри него
        for menu_sel in [
            '[aria-label*="еню" i]', '[aria-label*="menu" i]', '[aria-label*="Menu"]',
            'button[class*="menu"]', 'button[class*="hamburger"]', '[class*="hamburger"]',
            'nav button', 'header button',
        ]:
            if await self._try_click(menu_sel, timeout_ms=3000):
                logger.info("Opened menu, looking for login link")
                await self._human_delay()
                break

        login_selectors = [
            'a:has-text("Вход")', 'button:has-text("Вход")',
            'a:has-text("Войти")', 'button:has-text("Войти")',
            'span:has-text("Вход")', 'span:has-text("Войти")',
            '[role="button"]:has-text("Вход")', '[role="button"]:has-text("Войти")',
            'a:has-text("Sign in")', 'button:has-text("Sign in")',
            'a:has-text("Log in")', 'button:has-text("Log in")',
            'a:has-text("Личный кабинет")', 'button:has-text("Личный кабинет")',
            '[href*="login"]', '[href*="signin"]',
            'header a[href*="login"]', 'header a[href*="signin"]',
        ]
        for sel in login_selectors:
            try:
                if await self._try_click(sel, timeout_ms=4000):
                    login_clicked = True
                    logger.info("Login link clicked: %s", sel)
                    break
            except Exception:  # noqa: BLE001
                continue
        if not login_clicked:
            # Пробуем прямой переход на /login — с уже открытой сессией страны иногда срабатывает
            login_url = base_url.rstrip("/") + "/login"
            logger.info("Login button not found, trying direct navigation to %s", login_url)
            resp = await page.goto(login_url, wait_until="domcontentloaded", timeout=15000)
            if resp and resp.status >= 400:
                try:
                    debug_dir = BASE_DIR / "logs"
                    debug_dir.mkdir(parents=True, exist_ok=True)
                    path = debug_dir / f"main_without_login_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.png"
                    await page.screenshot(path=str(path), full_page=True)
                    logger.warning("Не найдена кнопка входа на главной, скриншот: %s", path)
                except Exception as e:  # noqa: BLE001
                    logger.warning("Не удалось сохранить скриншот: %s", e)
                raise RuntimeError("Не найдена кнопка входа на главной странице VFS")
            login_clicked = True
        await self._human_delay()
        await self._check_captcha()

        # Пробуем разные селекторы поля email (разметка VFS может отличаться)
        email_selectors = [
            'input[type="email"]',
            'input[name="email"]',
            'input[placeholder*="email"]',
            'input[placeholder*="@"]',
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

        # Fallback: пытаемся найти email-поле более «умно»,
        # если ни один из заранее заданных селекторов не сработал.
        if not email_filled:
            try:
                # Ищем форму, в которой есть поле пароля — логично, что там же и логин/e-mail.
                forms = await page.query_selector_all("form")
                for form in forms:
                    pwd_input = await form.query_selector('input[type="password"], input[name*="password" i]')
                    if not pwd_input:
                        continue

                    # В пределах этой формы ищем подходящее текстовое/почтовое поле
                    candidates = await form.query_selector_all(
                        'input[type="email"], '
                        'input[type="text"], '
                        'input[name*="email" i], '
                        'input[name*="login" i], '
                        'input[name*="user" i], '
                        'input[placeholder*="email" i], '
                        'input[placeholder*="@"]'
                    )
                    for el in candidates:
                        t = (await el.get_attribute("type")) or ""
                        # Отсекаем скрытые/технические инпуты
                        if t.lower() in {"hidden", "submit", "button"}:
                            continue
                        await el.scroll_into_view_if_needed()
                        await el.fill(email)
                        email_filled = True
                        break

                    if email_filled:
                        break
            except Exception as e:  # noqa: BLE001
                logger.warning("Fallback email field search failed: %s", e)

        if not email_filled:
            # Сохраняем скриншот для отладки — что видит бот вместо формы
            try:
                debug_dir = BASE_DIR / "logs"
                debug_dir.mkdir(parents=True, exist_ok=True)
                path = debug_dir / f"login_page_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.png"
                await page.screenshot(path=str(path), full_page=True)
                logger.warning("Скриншот страницы логина сохранён: %s", path)
            except Exception as e:  # noqa: BLE001
                logger.warning("Не удалось сохранить скриншот: %s", e)
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

        # Кнопка «Войти»
        submit_selectors = [
            'button:has-text("Войти")',
            'button[type="submit"]',
            'input[type="submit"]',
            'a:has-text("Войти")',
        ]
        submitted = False
        for sel in submit_selectors:
            try:
                el = await page.wait_for_selector(sel, timeout=2000)
                if el:
                    await self._human_click(sel)
                    submitted = True
                    break
            except Exception:  # noqa: BLE001
                continue
        if not submitted:
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

