"""
🤖 ТЕЛЕГРАМ-БОТ ДЛЯ ПОИСКА СЛОТОВ FFC.TEAM
Версия 2.0 - Работает 24/7 на Railway
"""

import os
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Set

import requests
from telegram import Update
from telegram.error import Conflict
from telegram.ext import Application, CommandHandler, ContextTypes

# ===================== НАСТРОЙКА ЛОГИРОВАНИЯ =====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ===================== КЛАСС ПАРСЕРА FFC =====================
class FFCParser:
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Content-Type": "application/json",
        }
        self.venues = {
            "seliger": {
                "id": "de503e35-1a81-430c-b919-c2e8fac638c2",
                "name": "Селигерская (Футбольный манеж)",
            },
            "kantem": {
                "id": "9da0ba06-e433-43cd-b955-1981d0734b9f",
                "name": "Кантемировская",
            },
        }

    def get_search_period(self):
        """Рассчитываем период: сегодня + следующая неделя"""
        today = datetime.now()
        days_to_weekend = 6 - today.weekday()  # дней до воскресенья
        total_days = days_to_weekend + 7      # + следующая неделя
        return today, total_days

    def fetch_slots_from_api(self, venue_id: str, date_str: str):
        """Получаем слоты с API FFC"""
        url = f"https://api.vivacrm.ru/end-user/api/v1/iSkq6G/products/master-services/{venue_id}/timeslots"
        payload = {"date": date_str, "trainers": {"type": "NO_TRAINER"}}
        
        try:
            response = requests.post(url, json=payload, headers=self.headers, timeout=10)
            data = response.json()
            return data.get("byTrainer", {}).get("NO_TRAINER", {}).get("slots", [])
        except Exception as e:
            logger.error(f"Ошибка API для {date_str}: {e}")
            return []

    def parse_duration(self, duration_str: str) -> int:
        """Преобразуем PT1H30M в минуты"""
        if not duration_str or not duration_str.startswith('PT'):
            return 30
        
        duration_str = duration_str[2:]  # Убираем 'PT'
        minutes = 0
        
        if 'H' in duration_str:
            hours_part, duration_str = duration_str.split('H')
            minutes += int(hours_part) * 60
        
        if 'M' in duration_str:
            minutes_part = duration_str.replace('M', '')
            if minutes_part:
                minutes += int(minutes_part)
        
        return minutes if minutes > 0 else 30

    def parse_all_slots(self, venue_id: str) -> List[Dict]:
        """Основной метод парсинга слотов"""
        start_date, total_days = self.get_search_period()
        all_slots = []
        
        # Собираем данные за весь период
        for day_offset in range(total_days + 1):
            current_date = start_date + timedelta(days=day_offset)
            date_str = current_date.strftime("%Y-%m-%d")
            raw_slots = self.fetch_slots_from_api(venue_id, date_str)
            
            for slot_group in raw_slots:
                for slot in slot_group:
                    try:
                        time_from = slot.get("timeFrom", "")
                        time_to = slot.get("timeTo", "")
                        duration = slot.get("availableDuration", "PT30M")
                        
                        dt_from = datetime.fromisoformat(time_from.replace('Z', '+00:00'))
                        dt_to = datetime.fromisoformat(time_to.replace('Z', '+00:00'))
                        
                        all_slots.append({
                            'datetime': dt_from,
                            'date': dt_from.strftime("%d.%m.%Y"),
                            'weekday': ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"][dt_from.weekday()],
                            'weekday_num': dt_from.weekday(),
                            'start': dt_from.strftime("%H:%M"),
                            'end': dt_to.strftime("%H:%M"),
                            'time': f"{dt_from.strftime('%H:%M')}-{dt_to.strftime('%H:%M')}",
                            'room': slot.get("roomName", ""),
                            'price': slot.get("price", {}).get("from", 0),
                            'duration_minutes': self.parse_duration(duration),
                            'unique_key': f"{dt_from.strftime('%Y%m%d%H%M')}"
                        })
                    except Exception as e:
                        continue
        
        return self.filter_slots_intelligently(all_slots)

    def filter_slots_intelligently(self, slots: List[Dict]) -> List[Dict]:
        """Умная фильтрация слотов по правилам FFC"""
        if not slots:
            return []
        
        # 1. Убираем дубликаты
        unique_slots = []
        seen_keys: Set[str] = set()
        for slot in slots:
            key = slot['unique_key']
            if key not in seen_keys:
                seen_keys.add(key)
                unique_slots.append(slot)
        
        # 2. Сортируем по дате и времени
        unique_slots.sort(key=lambda x: (x['date'], x['start']))
        
        # 3. Фильтруем слоты с duration=PT30M после слотов с большей длительностью
        filtered_by_duration = []
        i = 0
        n = len(unique_slots)
        
        while i < n:
            current_slot = unique_slots[i]
            
            # Пропускаем слоты, которые являются продолжением предыдущего
            if i + 1 < n:
                next_slot = unique_slots[i + 1]
                if (next_slot['date'] == current_slot['date'] and 
                    next_slot['start'] == current_slot['end'] and
                    current_slot['duration_minutes'] > 30 and 
                    next_slot['duration_minutes'] == 30):
                    i += 1  # Пропускаем следующий слот
            
            filtered_by_duration.append(current_slot)
            i += 1
        
        # 4. Фильтрация по времени: будни с 18:30, выходные все
        final_slots = []
        for slot in filtered_by_duration:
            is_weekday = slot['weekday_num'] < 5  # Пн-Пт
            
            if is_weekday:
                # Проверяем, чтобы слот начинался не раньше 18:30
                hours, minutes = map(int, slot['start'].split(':'))
                total_minutes = hours * 60 + minutes
                if total_minutes >= 1110:  # 18:30 = 1110 минут
                    final_slots.append(slot)
            else:
                # Выходные - все слоты
                final_slots.append(slot)
        
        # 5. Форматируем результат
        return [{
            'date': slot['date'],
            'weekday': slot['weekday'],
            'time': slot['time'],
            'price': f"{int(slot['price']):,} руб.".replace(',', ' ')
        } for slot in final_slots]

    def get_all_venues_slots(self) -> Dict:
        """Получаем слоты для всех площадок"""
        results = {}
        
        for venue_key, venue_info in self.venues.items():
            try:
                slots = self.parse_all_slots(venue_info['id'])
                results[venue_key] = {
                    'name': venue_info['name'],
                    'slots': slots,
                    'count': len(slots)
                }
            except Exception as e:
                logger.error(f"Ошибка для {venue_info['name']}: {e}")
                results[venue_key] = {'name': venue_info['name'], 'slots': [], 'count': 0}
        
        return results

# ===================== СОЗДАЕМ ПАРСЕР =====================
parser = FFCParser()
TOKEN = os.environ.get("BOT_TOKEN")  # Токен берется из Railway Variables

# ===================== КОМАНДЫ ТЕЛЕГРАМ-БОТА =====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user = update.effective_user
    welcome_text = (
        f"Привет, {user.first_name}! 👋\n\n"
        "⚽ *Я — бот для поиска свободных футбольных слотов на FFC.Team*\n\n"
        "📋 *Доступные команды:*\n"
        "• /slots — найти свободные слоты\n"
        "• /venues — список площадок\n"
        "• /help — помощь\n\n"
        "⚙️ *Автофильтрация:*\n"
        "• Будни (Пн-Пт) — только слоты с 18:30\n"
        "• Выходные — все доступные слоты\n\n"
        "Жми /slots чтобы начать поиск! 🎯"
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def venues_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /venues"""
    text = "🏟️ *ДОСТУПНЫЕ ПЛОЩАДКИ:*\n\n"
    for venue in parser.venues.values():
        text += f"• {venue['name']}\n"
    text += "\n🔍 Используйте /slots для поиска слотов."
    await update.message.reply_text(text, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    text = (
        "🆘 *ПОМОЩЬ*\n\n"
        "*/slots* — основной поиск слотов на 2 недели вперед\n"
        "*/venues* — список всех площадок\n"
        "*/start* — это сообщение\n\n"
        "📊 *Как это работает:*\n"
        "1. Бот проверяет доступность слотов на 2 недели\n"
        "2. В будни показывает только слоты с 18:30\n"
        "3. В выходные показывает все свободные слоты\n"
        "4. Данные обновляются в реальном времени\n\n"
        "❓ Есть вопросы? Обращайтесь к разработчику!"
    )
    await update.message.reply_text(text, parse_mode='Markdown')

async def slots_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /slots — ГЛАВНАЯ ФУНКЦИЯ"""
    # Отправляем сообщение о начале поиска
    message = await update.message.reply_text(
        "🔍 *Ищу свободные слоты...*\n"
        "_Проверяю доступность на 2 недели вперед. Это займет ~10 секунд ⏳_",
        parse_mode='Markdown'
    )
    
    try:
        # Получаем все слоты
        results = parser.get_all_venues_slots()
        
        if not results:
            output = "❌ *Не удалось получить данные от сервера FFC.*"
        else:
            messages = []
            total_slots_found = 0
            
            for venue_data in results.values():
                slots = venue_data['slots']
                if not slots:
                    continue
                
                venue_msg = f"🏟️ *{venue_data['name']}*\n"
                current_date = None
                
                for slot in slots:
                    if slot['date'] != current_date:
                        current_date = slot['date']
                        venue_msg += f"\n📅 *{current_date}* ({slot['weekday']}):\n"
                    
                    venue_msg += f"• {slot['time']} — {slot['price']}\n"
                    total_slots_found += 1
                
                venue_msg += f"\nВсего: {len(slots)} слотов\n"
                messages.append(venue_msg)
            
            if not messages:
                output = (
                    "🎯 *На ближайшие 2 недели свободных слотов не найдено.*\n\n"
                    "_Попробуйте изменить параметры поиска или проверьте позже._"
                )
            else:
                header = f"⚽ *СВОБОДНЫЕ СЛОТЫ FFC.TEAM*\n_Найдено {total_slots_found} слотов_\n\n"
                footer = "\n📝 _Примечание: В будни показываются только слоты с 18:30 и позже._"
                output = header + "="*40 + "\n".join(messages) + footer
        
        # Редактируем сообщение с результатами
        await message.edit_text(output, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Критическая ошибка в slots_command: {e}")
        error_text = (
            "❌ *Произошла непредвиденная ошибка*\n\n"
            "Пожалуйста, попробуйте еще раз через пару минут.\n"
            "Если ошибка повторяется — свяжитесь с разработчиком."
        )
        await message.edit_text(error_text, parse_mode='Markdown')

# ===================== ЗАПУСК БОТА =====================

async def setup_bot_commands(application):
    """Устанавливаем меню команд в Telegram"""
    await application.bot.set_my_commands([
        ("start", "Запустить бота"),
        ("slots", "Найти свободные слоты ⭐"),
        ("venues", "Список площадок"),
        ("help", "Помощь по использованию"),
    ])
    logger.info("✅ Меню команд Telegram установлено")

def main():
    """Главная функция запуска бота"""
    # Проверяем токен
    if not TOKEN:
        logger.error("❌ ОШИБКА: Токен бота не найден!")
        logger.error("Добавьте переменную BOT_TOKEN в Railway → Variables")
        return
    
    logger.info("=" * 60)
    logger.info("🚀 ЗАПУСК БОТА FFC НА RAILWAY")
    logger.info(f"🤖 Токен: {TOKEN[:10]}...{TOKEN[-10:]}")
    logger.info("=" * 60)
    
    # Создаем приложение с обработкой конфликтов
    async def post_init(app):
        # КРИТИЧЕСКИ ВАЖНО: сбрасываем все старые соединения
        await app.bot.delete_webhook(drop_pending_updates=True)
        await setup_bot_commands(app)
        logger.info("✅ Конфликты сброшены, бот готов к работе")
    
    try:
        # Создаем и настраиваем приложение
        application = Application.builder() \
            .token(TOKEN) \
            .post_init(post_init) \
            .build()
        
        # Регистрируем обработчики команд
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("slots", slots_command))
        application.add_handler(CommandHandler("venues", venues_command))
        application.add_handler(CommandHandler("help", help_command))
        
        # Запускаем бота в режиме постоянного опроса
        logger.info("✅ Бот запущен и ожидает команд...")
        logger.info("👉 Напишите /start боту в Telegram")
        
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            close_loop=False
        )
        
    except Conflict as e:
        logger.error(f"🚨 КОНФЛИКТ: Запущено несколько ботов одновременно")
        logger.error("Решение: Подождите 2 минуты или перезапустите в Railway")
        logger.error(f"Детали: {e}")
    except Exception as e:
        logger.error(f"🚨 КРИТИЧЕСКАЯ ОШИБКА: {type(e).__name__}: {e}")
        import traceback
        logger.error(traceback.format_exc())

# ===================== ТОЧКА ВХОДА =====================
if __name__ == "__main__":
    # Проверяем, что мы на Railway (или локально для теста)
    if "RAILWAY_ENVIRONMENT" in os.environ:
        logger.info("🌐 Среда: Railway (продакшн)")
    else:
        logger.info("💻 Среда: Локальная (разработка)")
    
    # Запускаем бота
    main()
