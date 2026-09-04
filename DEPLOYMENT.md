# Инструкция по развертыванию Discord бота на сервере

## Шаг 1: Подготовка сервера

### 1.1. Подключитесь к серверу по SSH
```bash
ssh user@your-server-ip
```

### 1.2. Обновите систему (для Ubuntu/Debian)
```bash
sudo apt update && sudo apt upgrade -y
```

### 1.3. Установите Python 3.11+ и необходимые пакеты

**Для Ubuntu 22.04 и новее:**
Python 3.11 доступен в стандартных репозиториях:
```bash
sudo apt install python3.11 python3.11-venv python3-pip -y
```

**Для Ubuntu 20.04 и старше (если Python 3.11 не найден):**
Добавьте PPA deadsnakes для доступа к Python 3.11:
```bash
# Установите необходимые пакеты для добавления PPA
sudo apt install software-properties-common -y

# Добавьте PPA deadsnakes
sudo add-apt-repository ppa:deadsnakes/ppa -y

# Обновите список пакетов
sudo apt update

# Установите Python 3.11
sudo apt install python3.11 python3.11-venv python3.11-dev -y

# Установите pip для Python 3.11
curl -sS https://bootstrap.pypa.io/get-pip.py | python3.11
```

**Для Debian:**
Python 3.11 доступен в Debian 12 (Bookworm) и новее. Для более старых версий:
```bash
# Проверьте версию Debian
cat /etc/debian_version

# Для Debian 11 и старше, используйте pyenv или компилируйте из исходников
# Или обновитесь до Debian 12
```

**Установка остальных пакетов:**
```bash
# TeXLive (для компиляции PDF)
sudo apt install texlive-full -y

# Git (если еще не установлен)
sudo apt install git -y
```

## Шаг 2: Копирование проекта на сервер

### 2.1. Скопируйте проект на сервер

**Вариант A: Через SCP (с вашего локального компьютера)**
```bash
scp -r "/path/to/osanwe" user@your-server-ip:/home/user/rosetta-bot
```

**Вариант B: Через Git (если проект в репозитории)**
```bash
# На сервере
cd ~
git clone <your-repo-url> rosetta-bot
cd rosetta-bot
```

**Вариант C: Через rsync (рекомендуется)**
```bash
rsync -avz --exclude 'venv' --exclude '__pycache__' --exclude '*.pyc' \
  "/path/to/osanwe/" \
  user@your-server-ip:/home/user/rosetta-bot/
```

### 2.2. Перейдите в директорию проекта
```bash
cd ~/rosetta-bot
```

## Шаг 3: Настройка окружения

### 3.1. Создайте виртуальное окружение
```bash
python3.11 -m venv venv
source venv/bin/activate
```

### 3.2. Установите зависимости
```bash
pip install --upgrade pip
pip install -r requirements.txt
pip install discord.py  # Если еще не установлено
```

### 3.3. Создайте файл .env
```bash
nano .env
```

Добавьте в файл следующие переменные:
```env
# Discord Bot Token
DISCORD_BOT_TOKEN=ваш_токен_дискорд_бота

# OpenAI API
OPENAI_API_KEY=ваш_ключ_openai

# Notion (опционально)
NOTION_TOKEN=ваш_токен_notion
NOTION_DATABASE_ID=ваш_database_id

# Другие настройки (опционально)
LOG_LEVEL=INFO
```

Сохраните файл (Ctrl+O, Enter, Ctrl+X)

### 3.4. Проверьте, что бот запускается
```bash
python3 discord_bot.py
```

Если видите сообщение "Бот запущен как...", нажмите Ctrl+C для остановки.

## Шаг 4: Создание systemd service

### 4.1. Создайте файл сервиса
```bash
sudo nano /etc/systemd/system/rosetta-bot.service
```

### 4.2. Вставьте следующую конфигурацию

**ВАЖНО:** Замените `/home/user/rosetta-bot` на реальный путь к вашему проекту!

```ini
[Unit]
Description=Rosetta Discord Bot
After=network.target

[Service]
Type=simple
User=your-username
WorkingDirectory=/home/user/rosetta-bot
Environment="PATH=/home/user/rosetta-bot/venv/bin"
ExecStart=/home/user/rosetta-bot/venv/bin/python3 /home/user/rosetta-bot/discord_bot.py
Restart=always
RestartSec=10
StandardOutput=append:/home/user/rosetta-bot/logs/discord_bot.log
StandardError=append:/home/user/rosetta-bot/logs/discord_bot_error.log

[Install]
WantedBy=multi-user.target
```

**Замените:**
- `your-username` на ваше имя пользователя на сервере
- `/home/user/rosetta-bot` на реальный путь к проекту

### 4.3. Сохраните файл (Ctrl+O, Enter, Ctrl+X)

### 4.4. Перезагрузите systemd
```bash
sudo systemctl daemon-reload
```

### 4.5. Включите автозапуск
```bash
sudo systemctl enable rosetta-bot.service
```

### 4.6. Запустите бота
```bash
sudo systemctl start rosetta-bot.service
```

### 4.7. Проверьте статус
```bash
sudo systemctl status rosetta-bot.service
```

Вы должны увидеть "active (running)".

## Шаг 5: Проверка работы

### 5.1. Просмотр логов в реальном времени
```bash
sudo journalctl -u rosetta-bot.service -f
```

Или логи из файлов:
```bash
tail -f ~/rosetta-bot/logs/discord_bot.log
```

### 5.2. Проверьте в Discord
Откройте Discord и попробуйте использовать команду `/rosetta` с какой-нибудь статьей.

## Полезные команды

### Управление сервисом
```bash
# Остановить бота
sudo systemctl stop rosetta-bot.service

# Запустить бота
sudo systemctl start rosetta-bot.service

# Перезапустить бота
sudo systemctl restart rosetta-bot.service

# Посмотреть статус
sudo systemctl status rosetta-bot.service

# Отключить автозапуск
sudo systemctl disable rosetta-bot.service

# Включить автозапуск
sudo systemctl enable rosetta-bot.service
```

### Просмотр логов
```bash
# Последние 100 строк логов
sudo journalctl -u rosetta-bot.service -n 100

# Логи за сегодня
sudo journalctl -u rosetta-bot.service --since today

# Логи с фильтром по ошибкам
sudo journalctl -u rosetta-bot.service | grep -i error
```

## Решение проблем

### Бот не запускается
1. Проверьте логи: `sudo journalctl -u rosetta-bot.service -n 50`
2. Проверьте, что все переменные в `.env` установлены правильно
3. Проверьте права доступа: `ls -la ~/rosetta-bot`

### Бот падает
1. Проверьте логи ошибок: `cat ~/rosetta-bot/logs/discord_bot_error.log`
2. Убедитесь, что TeXLive установлен: `which pdflatex`
3. Проверьте доступ к интернету с сервера

### Обновление кода
```bash
# Остановите бота
sudo systemctl stop rosetta-bot.service

# Обновите код (через git или scp)
cd ~/rosetta-bot
# ... обновите файлы ...

# Установите новые зависимости (если нужно)
source venv/bin/activate
pip install -r requirements.txt

# Запустите бота снова
sudo systemctl start rosetta-bot.service
```

## Безопасность

1. **Не коммитьте `.env` файл в Git** - он содержит секретные ключи
2. **Ограничьте доступ к файлам:**
   ```bash
   chmod 600 ~/rosetta-bot/.env
   ```
3. **Используйте firewall** для защиты сервера
4. **Регулярно обновляйте зависимости:**
   ```bash
   source venv/bin/activate
   pip install --upgrade -r requirements.txt
   ```

