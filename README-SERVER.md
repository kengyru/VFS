# Развёртывание бота на сервере (VPS)

## Важно: ручной логин vs сервер

- **VFS_MANUAL_LOGIN=1** — бот открывает Chrome, ты вручную логинишься. Это нужно на своём компьютере (Windows/Mac), где ты видишь браузер.
- **На сервере без экрана** — ручной логин невозможен. Используй **автологин**:
  - Убери или закомментируй `VFS_MANUAL_LOGIN` в `.env` (или поставь `VFS_MANUAL_LOGIN=0`)
  - Бот сам введёт логин и пароль

VFS иногда отдаёт 403 для автоматических запросов. Если на сервере не получается — попробуй другой IP (VPS в другом регионе) или запускай на своём компьютере.

---

## Вариант 1: Docker (проще всего)

### Требования
- Linux VPS (Ubuntu 22.04 и т.п.)
- Docker и Docker Compose

### Шаги

1. Подключись по SSH:
   ```bash
   ssh user@your-server-ip
   ```

2. Установи Docker (если ещё нет):
   ```bash
   curl -fsSL https://get.docker.com | sh
   sudo usermod -aG docker $USER
   # Выйди и зайди снова (чтобы группа применилась)
   ```

3. Склонируй репозиторий:
   ```bash
   git clone https://github.com/kengyru/VFS.git vfs-bot
   cd vfs-bot
   ```

4. Создай `.env` (скопируй из `.env.example` и заполни):
   ```bash
   cp .env.example .env
   nano .env   # или vim
   ```
   Для сервера **не ставь** `VFS_MANUAL_LOGIN=1` — оставь автологин.

5. Запусти:
   ```bash
   docker compose build
   docker compose up -d
   ```

6. Проверь логи:
   ```bash
   docker compose logs -f vfs-bot
   ```

7. Управление:
   ```bash
   docker compose down    # остановить
   docker compose up -d   # запустить
   docker compose pull && docker compose up -d --build  # обновить
   ```

---

## Вариант 2: Без Docker (systemd)

### Шаги

1. Установи зависимости:
   ```bash
   sudo apt update
   sudo apt install -y python3 python3-venv python3-pip
   ```

2. Склонируй и настрой:
   ```bash
   git clone https://github.com/kengyru/VFS.git vfs-bot
   cd vfs-bot
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   playwright install --with-deps chromium
   ```

3. Создай `.env` (без `VFS_MANUAL_LOGIN=1` для сервера).

4. Установи systemd-сервис:
   ```bash
   # Отредактируй deploy/vfs-bot.service: YOUR_USER и путь к проекту
   sudo nano deploy/vfs-bot.service
   # Замени YOUR_USER на своего пользователя
   # Замени /opt/vfs-bot на /home/user/vfs-bot (или куда положил)

   sudo cp deploy/vfs-bot.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable vfs-bot
   sudo systemctl start vfs-bot
   sudo systemctl status vfs-bot
   ```

5. Логи: `journalctl -u vfs-bot -f`

---

## Обновление кода

```bash
cd vfs-bot
git pull origin main
# Docker:
docker compose up -d --build

# Без Docker:
sudo systemctl restart vfs-bot
```

---

## Если VFS блокирует (403)

- Попробуй VPS в другой стране
- Или запускай бота на своём компьютере (Windows) с `VFS_MANUAL_LOGIN=1` и ручным логином
