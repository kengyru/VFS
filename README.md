# VFS Global Slot Monitor

Telegram-бот для мониторинга свободных слотов записи на визу в Болгарию через сайт VFS Global. Найденные слоты приходят админу в Telegram.

---

## 1. Получение токена бота

1. Открой Telegram, найди бота **@BotFather**.
2. Отправь команду `/newbot`.
3. Введи имя бота (например: `VFS Slot Monitor`) и username (например: `my_vfs_bot`).
4. BotFather пришлёт **токен** вида `123456789:AAH...`. Сохрани его — это `BOT_TOKEN`.

---

## 2. Узнать свой Telegram ID (ADMIN_CHAT_ID)

1. Найди в Telegram бота **@userinfobot**.
2. Нажми **Start**.
3. Бот пришлёт сообщение с полем **Your user ID** — это число и есть `ADMIN_CHAT_ID` (например: `174443871`).

---

## 3. Настройка .env

В корне проекта (`vfs-bot`) создай файл **`.env`** (скопируй из `.env.example` и заполни):

```env
BOT_TOKEN=токен_от_BotFather
ADMIN_CHAT_ID=твой_Telegram_ID

VFS_EMAIL=email_для_входа_на_VFS
VFS_PASSWORD=пароль_от_VFS

CHECK_INTERVAL=120
CHECK_INTERVAL_VARIATION=30

TARGET_MONTH=3
TARGET_DAYS=15,16,17
TARGET_DAYS_OF_WEEK=1,2,3,4,5
TARGET_TIME_START=07:00
TARGET_TIME_END=22:00
```

- **BOT_TOKEN** — токен от @BotFather.  
- **ADMIN_CHAT_ID** — только этот пользователь может управлять ботом и получать уведомления.  
- **VFS_EMAIL / VFS_PASSWORD** — учётные данные для входа на visa.vfsglobal.com.  
- Остальные переменные задают интервал проверок и фильтр по дате/времени слотов.

Файл `.env` не должен попадать в git (добавлен в `.gitignore`).

---

## 4. Запуск через Docker (рекомендуется на VPS)

### Требования

- На сервере установлены **Docker** и **Docker Compose**.
- Файл **`.env`** лежит в папке `vfs-bot` рядом с `docker-compose.yml`.

### Шаги на VPS

1. Скопируй на сервер папку проекта (например через `scp`, `rsync` или git):

   ```bash
   scp -r vfs-bot user@your-server-ip:/home/user/
   ```

2. Подключись по SSH:

   ```bash
   ssh user@your-server-ip
   cd /home/user/vfs-bot
   ```

3. Создай `.env` (если ещё не скопировал) и заполни переменные (токен, ADMIN_CHAT_ID, VFS_EMAIL, VFS_PASSWORD и т.д.).

4. Собери образ и запусти контейнер:

   ```bash
   docker compose build
   docker compose up -d
   ```

5. Проверь, что контейнер работает:

   ```bash
   docker compose ps
   docker compose logs -f vfs-bot
   ```

В Telegram напиши боту `/start` — должно прийти приветствие и результат проверки логина VFS.

### Остановка и перезапуск

```bash
docker compose down
docker compose up -d
```

### Обновление конфигурации

После изменения `.env` перезапусти контейнер:

```bash
docker compose down
docker compose up -d
```

---

## 4.1. Минимальный вариант: работа через уже открытый Chrome (CDP)

Если сайт VFS отдаёт 403 при запуске браузера через Playwright, можно подключать бота к **уже запущенному** Chrome — тогда сайт видит обычную сессию.

1. **Запусти Chrome с включённой отладкой** (один раз перед запуском бота):

   - **Windows (PowerShell):**
     ```powershell
     & "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222
     ```
   - **Linux / macOS:**
     ```bash
     google-chrome --remote-debugging-port=9222
     ```
   Окно Chrome должно оставаться открытым.

2. В **`.env`** добавь (или раскомментируй):
   ```env
   CHROME_CDP_URL=http://127.0.0.1:9222
   ```

3. Запусти бота как обычно (`python -m src.bot` или через Docker). Бот подключится к этому окну и будет использовать уже открытую вкладку (или новую в том же окне). При завершении работы бот только отключится, Chrome не закроется.

Если переменная `CHROME_CDP_URL` не задана, бот по-прежнему сам запускает браузер (как в п. 4).

---

## 5. Логи

- В папке **`logs/`** создаётся файл **`vfs_bot.log`** (ротация по размеру, несколько бэкапов).
- При запуске через Docker папка `logs` смонтирована в контейнер, логи лежат на хосте в `vfs-bot/logs/`.

Просмотр логов:

```bash
# локально
tail -f vfs-bot/logs/vfs_bot.log

# в Docker
docker compose logs -f vfs-bot
```

Скриншоты при капче и по `/test_login` сохраняются в `logs/`.

---

## 6. Запуск без Docker (systemd, Linux)

Установи зависимости и Playwright:

```bash
cd vfs-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install --with-deps chromium
```

Создай `.env` и запусти:

```bash
source .venv/bin/activate
python -m src.bot
```

Чтобы бот работал как сервис:

1. Отредактируй **`deploy/vfs-bot.service`**:
   - замени `YOUR_USER` на своего пользователя Linux;
   - замени `/opt/vfs-bot` на реальный путь к проекту (если положил в `/home/user/vfs-bot` — укажи его).

2. Установи unit и запусти:

```bash
sudo cp deploy/vfs-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable vfs-bot
sudo systemctl start vfs-bot
sudo systemctl status vfs-bot
```

Логи сервиса: `journalctl -u vfs-bot -f`.

---

## 7. Отладка: типичные проблемы

| Проблема | Что проверить |
|----------|----------------|
| `ValidationError: BOT_TOKEN / ADMIN_CHAT_ID missing` | Файл `.env` в папке `vfs-bot`, переменные без пробелов вокруг `=`, запуск из каталога `vfs-bot`. |
| Бот не отвечает в Telegram | Правильный `BOT_TOKEN`, интернет на сервере, контейнер запущен (`docker compose ps`). |
| «Не удалось авторизоваться в VFS» / таймаут на полях | Сайт VFS может блокировать IP (Cloudflare). Проверь вход вручную в браузере с того же IP; при блокировке нужен другой IP (другая сеть, VPS в другом регионе). |
| TargetClosedError (браузер закрыт) | Не закрывай окно Playwright при `headless=False`. Для продакшена оставь `headless=True`. |
| Капча в Telegram | Бот при обнаружении капчи пришлёт скриншот и приостановит мониторинг на 10 минут, затем продолжит. |

---

## 8. Структура проекта

```
vfs-bot/
├── .env              # секреты (не в git)
├── .env.example      # шаблон переменных
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── README.md
├── logs/             # логи и скриншоты
├── data/             # в Docker: кэш слотов и куки (slot_cache.json, storage_state.json)
└── src/
    ├── bot.py        # точка входа, Telegram-бот (aiogram)
    ├── config.py     # загрузка .env и конфигурации
    ├── browser.py    # Playwright: логин VFS, парсинг слотов
    ├── monitor.py    # фоновый цикл мониторинга, дедупликация, уведомления
    ├── models.py     # Pydantic-модели
    ├── handlers.py   # (при необходимости вынести хэндлеры)
    └── utils.py      # логирование, retry, задержки
```

После деплоя достаточно один раз настроить `.env` и запустить контейнер (или systemd). Дальнейшее управление — через кнопки и команды бота в Telegram.
