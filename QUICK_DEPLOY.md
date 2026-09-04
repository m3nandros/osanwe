# 🚀 Быстрое развертывание - Шпаргалка

## Вариант 1: Автоматический (рекомендуется)

```bash
# 1. Скопируйте проект на сервер
rsync -avz --exclude 'venv' --exclude '__pycache__' \
  "/path/to/osanwe/" \
  user@server-ip:~/rosetta-bot/

# 2. Подключитесь к серверу
ssh user@server-ip

# 3. Перейдите в директорию и запустите скрипт
cd ~/rosetta-bot
chmod +x deploy.sh
./deploy.sh

# 4. Создайте .env файл (если еще не создан)
nano .env
# Добавьте:
# DISCORD_BOT_TOKEN=ваш_токен
# OPENAI_API_KEY=ваш_ключ

# 5. Запустите бота
sudo systemctl start rosetta-bot.service
sudo systemctl status rosetta-bot.service
```

## Вариант 2: Ручной

### На сервере выполните:

```bash
# 1. Установите зависимости системы
sudo apt update

# Для Ubuntu 20.04 и старше: добавьте PPA deadsnakes
sudo apt install software-properties-common -y
sudo add-apt-repository ppa:deadsnakes/ppa -y
sudo apt update

# Установите Python 3.11 и остальные пакеты
sudo apt install python3.11 python3.11-venv python3.11-dev texlive-full git -y

# Установите pip для Python 3.11 (если не установлен)
curl -sS https://bootstrap.pypa.io/get-pip.py | python3.11

# 2. Перейдите в директорию проекта
cd ~/rosetta-bot

# 3. Создайте виртуальное окружение
python3.11 -m venv venv
source venv/bin/activate

# 4. Установите Python зависимости
pip install --upgrade pip
pip install -r requirements.txt

# 5. Создайте .env файл
nano .env
# Добавьте ваши токены

# 6. Настройте systemd
sudo nano /etc/systemd/system/rosetta-bot.service
# Скопируйте содержимое из rosetta-bot.service, заменив YOUR_USERNAME и пути

# 7. Запустите
sudo systemctl daemon-reload
sudo systemctl enable rosetta-bot.service
sudo systemctl start rosetta-bot.service
```

## Проверка работы

```bash
# Статус
sudo systemctl status rosetta-bot.service

# Логи в реальном времени
sudo journalctl -u rosetta-bot.service -f

# Остановка
sudo systemctl stop rosetta-bot.service

# Перезапуск
sudo systemctl restart rosetta-bot.service
```

## Что нужно подготовить перед деплоем

1. ✅ Discord Bot Token (из Discord Developer Portal)
2. ✅ OpenAI API Key
3. ✅ IP адрес сервера и доступ по SSH
4. ✅ Notion токен и Database ID (опционально)

## Полная инструкция

Смотрите [DEPLOYMENT.md](DEPLOYMENT.md) для подробной инструкции.

