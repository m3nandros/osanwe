#!/bin/bash
# Скрипт для быстрого развертывания на сервере
# Использование: ./deploy.sh

set -e

echo "🚀 Начинаем развертывание Rosetta Discord Bot..."

# Проверка, что мы на сервере
if [ "$EUID" -eq 0 ]; then 
   echo "❌ Не запускайте скрипт от root. Используйте обычного пользователя."
   exit 1
fi

# Переменные (измените под ваш сервер)
PROJECT_DIR="$HOME/rosetta-bot"
PYTHON_VERSION="python3.11"

echo "📁 Рабочая директория: $PROJECT_DIR"

# 1. Создание виртуального окружения
echo "📦 Создаем виртуальное окружение..."
if [ ! -d "$PROJECT_DIR/venv" ]; then
    $PYTHON_VERSION -m venv "$PROJECT_DIR/venv"
    echo "✅ Виртуальное окружение создано"
else
    echo "ℹ️  Виртуальное окружение уже существует"
fi

# 2. Активация и установка зависимостей
echo "📥 Устанавливаем зависимости..."
source "$PROJECT_DIR/venv/bin/activate"
pip install --upgrade pip
pip install -r "$PROJECT_DIR/requirements.txt"

# 3. Проверка .env файла
echo "🔐 Проверяем .env файл..."
if [ ! -f "$PROJECT_DIR/.env" ]; then
    echo "⚠️  Файл .env не найден!"
    echo "Создайте файл .env с необходимыми переменными:"
    echo "  DISCORD_BOT_TOKEN=..."
    echo "  OPENAI_API_KEY=..."
    echo "  NOTION_TOKEN=... (опционально)"
    echo "  NOTION_DATABASE_ID=... (опционально)"
    exit 1
fi

# 4. Проверка прав на .env
chmod 600 "$PROJECT_DIR/.env"
echo "✅ Права на .env установлены"

# 5. Создание директорий для логов
echo "📝 Создаем директории для логов..."
mkdir -p "$PROJECT_DIR/logs"
mkdir -p "$PROJECT_DIR/temp"
mkdir -p "$PROJECT_DIR/output"

# 6. Проверка TeXLive
echo "🔍 Проверяем TeXLive..."
if ! command -v pdflatex &> /dev/null; then
    echo "⚠️  TeXLive не установлен!"
    echo "Установите его командой:"
    echo "  sudo apt install texlive-full -y"
    exit 1
fi
echo "✅ TeXLive установлен"

# 7. Тестовый запуск
echo "🧪 Тестируем запуск бота (5 секунд)..."
timeout 5 "$PROJECT_DIR/venv/bin/python3" "$PROJECT_DIR/discord_bot.py" || true
echo "✅ Тест завершен"

# 8. Установка systemd service
echo "⚙️  Настраиваем systemd service..."
SERVICE_FILE="/etc/systemd/system/rosetta-bot.service"
CURRENT_USER=$(whoami)

if [ -f "$PROJECT_DIR/rosetta-bot.service" ]; then
    # Заменяем плейсхолдеры в файле сервиса
    sed "s|YOUR_USERNAME|$CURRENT_USER|g; s|/home/YOUR_USERNAME/rosetta-bot|$PROJECT_DIR|g" \
        "$PROJECT_DIR/rosetta-bot.service" | sudo tee "$SERVICE_FILE" > /dev/null
    
    sudo systemctl daemon-reload
    sudo systemctl enable rosetta-bot.service
    echo "✅ Systemd service настроен"
    
    echo ""
    echo "📋 Следующие шаги:"
    echo "  1. Запустите бота: sudo systemctl start rosetta-bot.service"
    echo "  2. Проверьте статус: sudo systemctl status rosetta-bot.service"
    echo "  3. Смотрите логи: sudo journalctl -u rosetta-bot.service -f"
else
    echo "⚠️  Файл rosetta-bot.service не найден. Создайте его вручную."
fi

echo ""
echo "✅ Развертывание завершено!"

