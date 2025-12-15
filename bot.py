"""
ПОЛНЫЙ КОД ТЕЛЕГРАМ-БОТА FFC.TEAM ДЛЯ RAILWAY (ИСПРАВЛЕННАЯ ВЕРСИЯ)
"""

import os
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Set

import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.error import Conflict

# ============================================
# 1. НАСТРОЙКА ЛОГИРОВАНИЯ
# ============================================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============================================
# 2. КЛАСС ПАРСЕРА
# ============================================
class FFCBotManager:
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

    def get_period(self):
        today = datetime.now()
        weekday = today.weekday()
        days_to_sunday = 6 - weekday
        total_days = days_to_sunday + 7
        return today, total_days

    def fetch_raw_slots(self, venue_id: str, date_str: str):
        api_url = f"https://api.vivacrm.ru/end-user/api/v1/iSkq6G/products/master-services/{venue_id}/timeslots"
        payload = {"date": date_str, "trainers": {"type": "NO_TRAINER"}}
        
        try:
            response = requests.post(api_url, json=payload, headers=self.headers, timeout=10)
            data = response.json()
            return data.get("byTrainer", {}).get("NO_TRAINER", {}).get("slots", [])
        except Exception as e:
            logger.error(f"Ошибка для {date_str}: {e}")
            return []

    def parse_slots(self, venue_id: str):
        start_date, total_days = self.get_period()
        all_raw_slots = []
        
        for day_offset in range(total_days + 1):
            current_date = start_date + timedelta(days=day_offset)
            date_str = current_date.strftime("%Y-%m-%d")
            raw_slots = self.fetch_raw_slots(venue_id, date_str)
            
            for slot_group in raw_slots:
                for slot in slot_group:
                    try:
                        time_from = slot.get("timeFrom", "")
                        time_to = slot.get("timeTo", "")
                        available_duration = slot.get("availableDuration", "PT30M")
                        
                        dt_from = datetime.fromisoformat(time_from.replace('Z', '+00:00'))
                        dt_to = datetime.fromisoformat(time_to.replace('Z', '+00:00'))
                        
                        # Преобразуем длительность
                        duration_str = available_duration
                        duration_minutes = 30
                        if duration_str.startswith('PT'):
                            duration_str = duration_str[2:]
                            if 'H' in duration_str:
                                hours_part, duration_str = duration_str.split('H')
                                duration_minutes = int(hours_part) * 60
                            if 'M' in duration_str:
                                minutes_part = duration_str.replace('M', '')
                                if minutes_part:
                                    duration_minutes += int(minutes_part)
                        
                        all_raw_slots.append({
                            'datetime': dt_from,
                            'date': dt_from.strftime("%d.%m.%Y"),
                            'weekday': ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"][dt_from.weekday()],
                            'weekday_num': dt_from.weekday(),
                            'start': dt_from.strftime("%H:%M"),
                            'end': dt_to.strftime("%H:%M"),
                            'time': f"{dt_from.strftime('%H:%M')}-{dt_to.strftime('%H:%M')}",
                            'room': slot.get("roomName", ""),
                            'price': slot.get("price", {}).get("from", 0),
                            'duration_minutes': duration_minutes,
                            'unique_key': f"{dt_from.strftime('%Y%m%d%H%M')}"
                        })
                    except Exception:
                        continue
        
        return self.smart_filter_slots(all_raw_slots)

    def smart_filter_slots(self, slots: List[Dict]):
        if not slots:
            return []
        
        # Убираем дубликаты
        unique_slots = []
        seen_keys: Set[str] = set()
        
        for slot in slots:
            key = slot['unique_key']
            if key not in seen_keys:
                seen_keys.add(key)
                unique_slots.append(slot)
        
        # Сортируем
        unique_slots.sort(key=lambda x: (x['date'], x['start']))
        
        # Фильтрация по duration
        filtered_by_duration = []
        i = 0
        n = len(unique_slots)
        
        while i < n:
            current_slot = unique_slots[i]
            
            if i + 1 < n:
                next_slot = unique_slots[i + 1]
                
                if (next_slot['date'] == current_slot['date'] and 
                    next_slot['start'] == current_slot['end']):
                    
                    if (current_slot['duration_minutes'] > 30 and 
                        next_slot['duration_minutes'] == 30):
                        i += 1
            
            filtered_by_duration.append(current_slot)
            i += 1
        
        # Фильтрация по времени (будни/выходные)
        final_slots = []
        for slot in filtered_by_duration:
            is_weekday = slot['weekday_num'] < 5
            
            if is_weekday:
                hours, minutes = map(int, slot['start'].split(':'))
                total_minutes = hours * 60 + minutes
                
                if total_minutes >= 1110:  # 18:30 или позже
                    final_slots.append(slot)
                else:
                    continue
            else:
                final_slots.append(slot)
        
        # Форматируем результат
        result = []
        for slot in final_slots:
            result.append({
                'date': slot['date'],
                'weekday': slot['weekday'],
                'time': slot['time'],
                'room': slot['room'],
                'price': f"{int(slot['price']):,} руб.".replace(',', ' ')
            })
        
        return result

    def get_all_slots(self):
        results = {}
        
        for venue_key, venue_info in self.venues.items():
            try:
                slots = self.parse_slots(venue_info['id'])
                results[venue_key] = {
                    'name': venue_info['name'],
                    'slots': slots,
                    'count': len(slots)
                }
            except Exception as e:
                logger.error(f"Ошибка для {venue_info['name']}: {e}")
                results[venue_key] = {'name': venue_info['name'], 'slots': [], 'count': 0}
        
        return results

# ============================================
# 3. СОЗДАЕМ ПАРСЕР И ПОЛУЧАЕМ ТОКЕН
# ============================================
parser = FFCBotManager()
TOKEN = os.environ.get("BOT_TOKEN")

# ============================================
# 4. ФУНКЦИИ-ОБРАБОТЧИКИ КОМАНД
# ============================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"Привет, {user.first_name}! 👋\n\n"
        "Я бот для поиска свободных футбольных слотов FFC.Team.\n\n"
        "📋 *Доступные команды:*\n"
        "/slots - Найти свободные слоты\n"
        "/venues - Список площадок\n"
        "/help - Помощь\n\n"
        "⚙️ *Фильтрация:* В будни показываю только слоты с 18:30.",
        parse_mode='Markdown'
    )

async def venues_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "🏟️ *ДОСТУПНЫЕ ПЛОЩАДКИ:*\n\n"
    for venue in parser.venues.values():
        text += f"• {venue['name']}\n"
    text += "\nИспользуйте /slots для поиска слотов."
    await update.message.reply_text(text, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🆘 *Помощь*\n\n"
        "*/slots* - основной поиск слотов на 2 недели вперед\n"
        "*/venues* - список всех площадок\n"
        "*/start* - это сообщение\n\n"
        "Бот автоматически фильтрует слоты:\n"
        "• Будни (пн-пт): только с 18:30\n"
        "• Выходные: все доступные слоты"
    )
    await update.message.reply_text(text, parse_mode='Markdown')

async def slots_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = await update.message.reply_text(
        "🔍 *Ищу свободные слоты...*\n\n"
        "Проверяю доступность на 2 недели вперед. Это займет ~10 секунд ⏳",
        parse_mode='Markdown'
    )
    
    try:
        results = parser.get_all_slots()
        
        if not results:
            output = "❌ Не удалось получить данные."
        else:
            messages = []
            for venue_data in results.values():
                slots = venue_data['slots']
                if not slots:
                    continue
                
                venue_msg = f"🏟️ *{venue_data['name']}*\n"
                current_date = None
                slot_count = 0
                
                for slot in slots:
                    if slot['date'] != current_date:
                        current_date = slot['date']
                        venue_msg += f"\n📅 *{current_date}* ({slot['weekday']}):\n"
                    
                    venue_msg += f"• {slot['time']} - {slot['price']}\n"
                    slot_count += 1
                
                venue_msg += f"\nВсего: {slot_count} слотов\n"
                messages.append(venue_msg)
            
            if not messages:
                output = "🎯 На ближайшие 2 недели свободных слотов не найдено."
            else:
                header = "⚽ *СВОБОДНЫЕ СЛОТЫ FFC.TEAM*\n\n"
                footer = "\n📝 _Примечание: В будни показываются только слоты с 18:30 и позже._"
                output = header + "="*40 + "\n".join(messages) + footer
        
        await message.edit_text(output, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Ошибка в slots_command: {e}")
        error_text = "❌ *Произошла ошибка*\n\nПопробуйте еще раз через пару минут."
        await message.edit_text(error_text, parse_mode='Markdown')

# ============================================
# 5. ОСНОВНОЙ ЗАПУСК БОТА (С ОБРАБОТКОЙ КОНФЛИКТОВ)
# ============================================

def main():
    """Основная функция запуска бота - с обработкой конфликтов"""
    if not TOKEN:
        logger.error("❌ Токен бота не найден! Проверьте переменную BOT_TOKEN в Railway.")
        return
    
    # 1. СОЗДАЕМ ПРИЛОЖЕНИЕ
    application = Application.builder().token(TOKEN).build()
    
    # 2. РЕГИСТРИРУЕМ КОМАНДЫ
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("slots", slots_command))
    application.add_handler(CommandHandler("venues", venues_command))
    application.add_handler(CommandHandler("help", help_command))
    
    # 3. УСТАНАВЛИВАЕМ МЕНЮ КОМАНД
    async def set_commands(app):
        await app.bot.set_my_commands([
            ("start", "Запустить бота"),
            ("slots", "Найти свободные слоты ⭐"),
            ("venues", "Список площадок"),
            ("help", "Помощь"),
        ])
        logger.info("✅ Меню команд установлено")
    
    application.post_init = set_commands
    
    # 4. ЗАПУСКАЕМ БОТА С ПОВТОРНЫМИ ПОПЫТКАМИ ПРИ КОНФЛИКТЕ
    logger.info("=" * 50)
    logger.info("🤖 БОТ FFC ЗАПУЩЕН НА RAILWAY!")
    logger.info("=" * 50)
    
    # Запускаем поллинг с обработкой ошибки Conflict
    retry_count = 0
    max_retries = 5
    
    while retry_count < max_retries:
        try:
            application.run_polling()
        except Conflict as e:
            retry_count += 1
            logger.warning(f"Конфликт: другой экземпляр бота. Попытка {retry_count}/{max_retries}")
            import time
            time.sleep(10)  # Ждем 10 секунд
        except Exception as e:
            logger.error(f"Неожиданная ошибка: {e}")
            break
    else:
        logger.error(f"Достигнут лимит попыток ({max_retries}). Бот остановлен.")

# ============================================
# 6. ТОЧКА ВХОДА - ЗАПУСК ПРОГРАММЫ
# ============================================
if __name__ == "__main__":
    # ГЛАВНОЕ ИСПРАВЛЕНИЕ: запускаем main() как обычную функцию
    main()
