"""
Telegram bot entrypoint built with aiogram 3.

Основной модуль Telegram-бота:
- /start, /test_login
- кнопки: Запустить мониторинг, Остановить, Статус
- FSM для состояния мониторинга
- мидлвара, которая пускает только админа по chat_id
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram import BaseMiddleware
from typing import Any, Awaitable, Callable, Dict

from .browser import VFSBrowser, CaptchaDetected
from .config import BASE_DIR, get_settings
from .monitor import MonitorService
from .utils import setup_logging


logger = logging.getLogger(__name__)


class AdminOnlyMiddleware(BaseMiddleware):
    """Allow only admin user to interact with bot."""

    def __init__(self, admin_chat_id: int) -> None:
        super().__init__()
        self.admin_chat_id = admin_chat_id

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any],
    ) -> Any:
        if getattr(event, "chat", None) and event.chat.id != self.admin_chat_id:
            await event.answer("Этот бот предназначен только для владельца.")
            return
        return await handler(event, data)


class MonitorStates(StatesGroup):
    idle = State()
    running = State()


def main() -> None:
    """Entry point for running the bot."""
    settings = get_settings()
    setup_logging()

    bot = Bot(
        settings.bot.token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    # Shared monitor service instance
    monitor = MonitorService(
        on_text=lambda text: _notify_admin_text(bot, settings.bot.admin_chat_id, text),
        on_slots=lambda title, slots: _notify_admin_slots(
            bot, settings.bot.admin_chat_id, title, slots
        ),
        on_captcha=lambda text, path: _notify_admin_captcha(
            bot, settings.bot.admin_chat_id, text, path
        ),
    )

    dp.message.middleware(AdminOnlyMiddleware(settings.bot.admin_chat_id))

    # region keyboards
    def main_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="▶️ Запустить мониторинг",
                        callback_data="start_monitoring",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⏹ Остановить",
                        callback_data="stop_monitoring",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="ℹ️ Статус",
                        callback_data="status",
                    )
                ],
            ]
        )

    # endregion

    @dp.message(Command("start"))
    async def cmd_start(message: Message, state: FSMContext) -> None:
        await state.set_state(MonitorStates.idle)
        await message.answer(
            "👋 Привет! Я бот для мониторинга свободных слотов VFS Global.\n\n"
            "Используй кнопки ниже для управления мониторингом.\n"
            "Доступна также команда /test_login для ручной проверки логина.\n\n"
            "Сейчас автоматически проверю учётные данные VFS...",
            reply_markup=main_keyboard(),
        )

        # Автоматическая проверка учётных данных при старте бота
        settings_local = get_settings()
        browser = VFSBrowser()
        try:
            ok = await browser.login(
                email=settings_local.vfs.email,
                password=settings_local.vfs.password,
            )
        except CaptchaDetected:
            await message.answer(
                "При автоматической проверке логина обнаружена капча/Cloudflare. "
                "Команда /test_login может дать больше информации."
            )
            ok = False
        except Exception as e:  # noqa: BLE001
            logger.exception("Error during auto login check: %s", e)
            await message.answer(f"Ошибка при автоматической проверке логина: {e!r}")
            ok = False
        finally:
            await browser.close()

        if ok:
            await message.answer("✅ Учётные данные VFS выглядят корректными.")
        else:
            await message.answer(
                "⚠️ Не удалось подтвердить учётные данные VFS. "
                "Проверьте логин/пароль и при необходимости используйте /test_login."
            )

    @dp.message(Command("test_login"))
    async def cmd_test_login(message: Message) -> None:
        """Проверка авторизации с текущими учётными данными."""
        await message.answer("Пробую авторизоваться в VFS, подождите...")
        settings_local = get_settings()
        browser = VFSBrowser()
        ok = False
        screenshot_path: Path | None = None
        try:
            ok = await browser.login(
                email=settings_local.vfs.email,
                password=settings_local.vfs.password,
            )
            screenshot_dir = BASE_DIR / "logs"
            screenshot_dir.mkdir(parents=True, exist_ok=True)
            screenshot_path = screenshot_dir / f"test_login_{datetime.now(timezone.utc).isoformat().replace(':', '-')}.png"
            await browser.screenshot(screenshot_path)
        except CaptchaDetected:
            await message.answer(
                "Во время тестового входа обнаружена капча/Cloudflare. "
                "Нужно вручную пройти защиту в браузере."
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("Error during /test_login: %s", e)
            await message.answer("Ошибка при тестовом входе. Подробности смотри в логах сервера.")
        finally:
            await browser.close()

        if ok:
            await message.answer("✅ Авторизация прошла успешно.")
        else:
            await message.answer("❌ Авторизация не удалась. Проверьте логин/пароль.")

        if screenshot_path and screenshot_path.exists():
            try:
                photo_file = BufferedInputFile(
                    screenshot_path.read_bytes(),
                    filename="test_login.png",
                )
                await message.answer_photo(
                    photo=photo_file,
                    caption="Скриншот после попытки входа.",
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("Failed to send /test_login screenshot: %s", e)

    @dp.callback_query(F.data == "start_monitoring")
    async def on_start_monitoring(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await monitor.start()
        await state.set_state(MonitorStates.running)
        await callback.message.edit_text(
            "Мониторинг запущен ✅", reply_markup=main_keyboard()
        )

    @dp.callback_query(F.data == "stop_monitoring")
    async def on_stop_monitoring(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await monitor.stop()
        await state.set_state(MonitorStates.idle)
        await callback.message.edit_text(
            "Мониторинг остановлен ⏹️", reply_markup=main_keyboard()
        )

    @dp.callback_query(F.data == "status")
    async def on_status(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        st = monitor.state
        text = (
            f"📊 <b>Статус мониторинга</b>\n"
            f"Состояние: {'запущен' if st.is_running else 'остановлен'}\n"
            f"Проверок выполнено: {st.checks_count}\n"
            f"Всего найдено слотов: {st.slots_found_total}\n"
        )
        if st.last_check_at:
            text += f"Последняя проверка: {st.last_check_at}\n"
        if st.last_error:
            text += f"Последняя ошибка: <code>{st.last_error}</code>\n"

        await callback.message.edit_text(text, reply_markup=main_keyboard())

    logger.info("Starting polling")
    asyncio.run(_run_polling(dp, bot))


async def _run_polling(dp: Dispatcher, bot: Bot) -> None:
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


async def _notify_admin_text(bot: Bot, admin_chat_id: int, text: str) -> None:
    try:
        await bot.send_message(chat_id=admin_chat_id, text=text)
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to send text notification: %s", e)


async def _notify_admin_slots(
    bot: Bot,
    admin_chat_id: int,
    title: str,
    slots: list[Slot],
) -> None:
    from .models import Slot  # local import to avoid circular

    lines = [title, ""]
    for slot in slots:
        # 15.03.2026 10:30 — Москва VFS (Виза Болгарии)
        dt_str = slot.date.strftime("%d.%m.%Y")
        t_str = slot.start_time.strftime("%H:%M")
        line = f"{dt_str} {t_str} — {slot.location} ({slot.service})"
        if slot.notes:
            line += f" — {slot.notes}"
        lines.append(line)

    await _notify_admin_text(bot, admin_chat_id, "\n".join(lines))


async def _notify_admin_captcha(
    bot: Bot,
    admin_chat_id: int,
    text: str,
    screenshot_path: Path,
) -> None:
    try:
        if screenshot_path.exists():
            await bot.send_photo(
                chat_id=admin_chat_id,
                photo=screenshot_path.read_bytes(),
                caption=text,
            )
        else:
            await _notify_admin_text(bot, admin_chat_id, text)
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to send captcha notification: %s", e)


if __name__ == "__main__":
    main()

