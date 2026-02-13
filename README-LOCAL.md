# Запуск бота на своём компьютере (без VPS)

Инструкция для клиента: как запустить VFS-бота на своём ПК, если сайт VFS открывается с вашего интернета.

---

## Требования

- Windows 10/11
- Python 3.11 или новее
- Интернет, с которого открывается visa.vfsglobal.com

---

## Шаг 1. Установи Python

1. Скачай Python с https://www.python.org/downloads/
2. При установке **обязательно** поставь галочку **"Add Python to PATH"**
3. Нажми "Install Now"
4. Перезапусти компьютер

---

## Шаг 2. Скачай проект

1. Открой https://github.com/kengyru/VFS
2. Нажми зелёную кнопку **Code** → **Download ZIP**
3. Распакуй архив (получится папка `VFS-main` или `VFS`) — например в `C:\VFS\`

---

## Шаг 3. Создай файл .env

1. В папке проекта найди файл **`.env.example`**
2. Скопируй его и переименуй копию в **`.env`**
3. Открой `.env` в Блокноте и заполни:

```
BOT_TOKEN=токен_от_BotFather
ADMIN_CHAT_ID=твой_Telegram_ID

VFS_EMAIL=твой_email_для_VFS
VFS_PASSWORD=пароль_от_VFS

CHECK_INTERVAL=120
CHECK_INTERVAL_VARIATION=30
TARGET_MONTH=3
TARGET_DAYS=15,16,17
TARGET_DAYS_OF_WEEK=1,2,3,4,5
TARGET_TIME_START=07:00
TARGET_TIME_END=22:00
```

Сохрани файл.

---

## Шаг 4. Установи зависимости

1. Открой **PowerShell** (поиск в меню Пуск → PowerShell)
2. Перейди в папку проекта:

```powershell
cd C:\VFS\VFS-main
```
(или `C:\VFS\VFS` — смотри, как называется папка после распаковки)

3. Создай виртуальное окружение и установи пакеты:

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
playwright install chromium
```

---

## Шаг 5. Запуск бота

В той же PowerShell (с активированным `.venv`):

```powershell
python -m src.bot
```

Должно появиться сообщение вида:
```
[INFO] Starting polling
[INFO] Run polling for bot ...
```

---

## Шаг 6. Проверка в Telegram

1. Открой своего бота в Telegram
2. Напиши `/start`
3. Используй кнопки: «Запустить мониторинг», «Статус», «Остановить»
4. Команда `/test_login` — проверка входа на VFS

---

## Остановка бота

В окне PowerShell нажми **Ctrl+C**.

---

## Важно

- Компьютер должен быть **включён**, пока бот работает
- Не закрывай окно PowerShell — бот работает в нём
- Если закрыть окно, бот остановится
- Можно свернуть окно и оставить бота работать

---

## Проблемы

| Ошибка | Что делать |
|--------|------------|
| `python не найден` | Переустанови Python с галочкой "Add to PATH" |
| `pip не найден` | Используй `python -m pip` вместо `pip` |
| Ошибка при `playwright install` | Запусти PowerShell от имени администратора |
| Бот не отвечает | Проверь BOT_TOKEN и ADMIN_CHAT_ID в .env |
