import os
import logging

# Включаем подробное логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

print("=" * 50)
print("ТЕСТ БОТА: СТАРТ")
print("=" * 50)

# 1. Проверяем переменные окружения
BOT_TOKEN = os.environ.get("BOT_TOKEN")
print(f"1. Токен из переменных окружения получен: {'ДА' if BOT_TOKEN else 'НЕТ'}")
if BOT_TOKEN:
    print(f"   (Длина токена: {len(BOT_TOKEN)} символов, первые 5: {BOT_TOKEN[:5]}...)")

# 2. Пробуем импортировать библиотеки
try:
    from telegram import __version__
    print(f"2. Библиотека 'python-telegram-bot' импортирована. Версия: {__version__}")
except ImportError as e:
    print(f"2. ОШИБКА импорта библиотеки 'telegram': {e}")
    exit(1)

# 3. Пробуем создать простого бота и получить информацию о нем
try:
    from telegram.ext import Application
    print("3. Пробую создать приложение бота...")
    
    # Создаем приложение, но НЕ запускаем polling
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Инициализируем приложение (это нужно, чтобы можно было вызвать getMe)
    await app.initialize()
    
    # Получаем информацию о боте
    bot_info = await app.bot.get_me()
    print(f"   ✅ УСПЕХ! Бот подключен к Telegram.")
    print(f"   Имя бота: @{bot_info.username}")
    print(f"   ID бота: {bot_info.id}")
    
    await app.shutdown()
    
except Exception as e:
    print(f"3. КРИТИЧЕСКАЯ ОШИБКА при создании бота: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()  # Эта строка напечатает полную трассировку ошибки
    exit(1)

print("=" * 50)
print("ТЕСТ БОТА: УСПЕШНО ЗАВЕРШЕН")
print("=" * 50)
print("Все проверки пройдены. Основной код бота должен работать.")
print("Если основной бот все еще не запускается, проблема в его логике (например,")
print("нет бесконечного цикла или ошибка в обработчиках команд).")
