#!/bin/bash
# Скрипт для запуска Discord бота с правильным окружением

# Переходим в директорию проекта
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Активируем виртуальное окружение
if [ ! -d "venv" ]; then
    echo "❌ Виртуальное окружение не найдено!"
    echo "Создаю виртуальное окружение..."
    python3 -m venv venv
fi

echo "🔧 Активирую виртуальное окружение..."
source venv/bin/activate

echo "📦 Устанавливаю/обновляю зависимости..."
pip install --upgrade pip > /dev/null 2>&1
pip install -r requirements.txt

echo "🚀 Запускаю Discord бота..."
echo "Использую Python из venv: $(which python3)"
echo "Путь к Python: $(python3 -c 'import sys; print(sys.executable)')"
echo ""

# Явно используем Python из активированного venv
python3 discord_bot.py

