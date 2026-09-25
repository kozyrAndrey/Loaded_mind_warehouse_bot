#!/usr/bin/env bash
set -e

echo "Создаю виртуальное окружение .venv..."
python3 -m venv .venv

echo "Активирую виртуальное окружение..."
source .venv/bin/activate

echo "Обновляю pip..."
python -m pip install --upgrade pip

echo "Устанавливаю зависимости из requirements.txt..."
python -m pip install -r requirements.txt

echo ""
echo "Готово ✅"
echo "Чтобы активировать окружение вручную:"
echo "source .venv/bin/activate"
echo ""
echo "Чтобы запустить бота:"
echo "python bot.py"
