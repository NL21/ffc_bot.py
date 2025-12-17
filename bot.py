"""
🤖 ТЕЛЕГРАМ-БОТ ДЛЯ ПОИСКА СЛОТОВ FFC.TEAM
Версия 3.0 - С улучшенной фильтрацией и корректным временем
"""

import os
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Set
import pytz  # Добавляем для работы с часовыми поясами

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

# ===================== КОНСТАНТЫ ФИЛЬТРАЦИИ =====================
# Часовой пояс Москвы (UTC+3)
MOSCOW_TZ = pytz.timezone('Europe/Moscow')

# График фильтрации (в минутах от начала суток)
FILTER_RULES = {
    'weekday': {  # Пн-Пт
        'start_minutes': 18 * 60 + 30,  # 18:30
        'end_minutes': 21 * 60 + 0      # 21:00
    },
    'weekend': {  # Сб-Вс
        'start_minutes': 8 * 60 + 30,   # 08:30
        'end_minutes': 21 * 60 + 30     # 21:30
    }
}

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
        
        # КЭШ: храним результаты на 5 минут
        self._cache = {
            'data': None,
            'timestamp': None,
            'ttl': 300  # 5 минут в секундах
        }
        logger.info("✅ Парсер инициализирован с кэшированием (5 минут)")

    def _is_cache_valid(self):
        """Проверяем, актуален ли кэш"""
        if not self._cache['data'] or not self._cache['timestamp']:
            return False
        
        from time import time
        current_time = time()
        cache_age = current_time - self._cache['timestamp']
        
        return cache_age < self._cache['ttl']

    def get_search_period(self):
        """Рассчитываем период: сегодня + следующая неделя"""
        today = datetime.now(MOSCOW_TZ)
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
                        
                        # Конвертируем в московское время
                        dt_from_moscow = dt_from.astimezone(MOSCOW_TZ)
                        dt_to_moscow = dt_to.astimezone(MOSCOW_TZ)
                        
                        all_slots.append({
                            'datetime': dt_from_moscow,
                            'date': dt_from_moscow.strftime("%d.%m.%Y"),
                            'weekday': ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"][dt_from_moscow.weekday()],
                            'weekday_num': dt_from_moscow.weekday(),
                            'start': dt_from_moscow.strftime("%H:%M"),
                            'end': dt_to_moscow.strftime("%H:%M"),
                            'time': f"{dt_from_moscow.strftime('%H:%M')}-{dt_to_moscow.strftime('%H:%M')}",
                            'room': slot.get("roomName", ""),
                            'price': slot.get("price", {}).get("from", 0),
                            'duration_minutes': self.parse_duration(duration),
                            'unique_key': f"{dt_from_moscow.strftime('%Y%m%d%H%M')}"
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
        
        # 4. ФИЛЬТРАЦИЯ ПО ВРЕМЕНИ НАЧАЛА И ОКОНЧАНИЯ
        final_slots = []
        for slot in filtered_by_duration:
            is_weekday = slot['weekday_num'] < 5  # Пн-Пт
            
            # Получаем правила фильтрации
            rules = FILTER_RULES['weekday' if is_weekday else 'weekend']
            
            # Преобразуем время начала и окончания в минуты
            start_hours, start_minutes = map(int, slot['start'].split(':'))
            end_hours, end_minutes = map(int, slot['end'].split(':'))
            
            start_total_minutes = start_hours * 60 + start_minutes
            end_total_minutes = end_hours * 60 + end_minutes
            
            # Проверяем по новым правилам:
            # 1. Слот должен начинаться НЕ РАНЬШЕ start_minutes
            # 2. Слот должен заканчиваться НЕ ПОЗЖЕ end_minutes
            if (start_total_minutes >= rules['start_minutes'] and 
                end_total_minutes <= rules['end_minutes']):
                final_slots.append(slot)
        
        # 5. Форматируем результат
        return [{
            'date': slot['date'],
            'weekday': slot['weekday'],
            'time': slot['time'],
            'price': f"{int(slot['price']):,} руб.".replace(',', ' ')
        } for slot in final_slots]

    def get_all_venues_slots(self) -> Dict:
        """Получаем слоты для всех площадок с кэшированием"""
        from time import time
        
        # Проверяем кэш
        if self._is_cache_valid():
            logger.info("📦 Используются кэшированные данные (парсинг не требуется)")
            # Обновляем timestamp кэша для отображения актуального времени
            self._cache['timestamp'] = time()
            return self._cache['data']
        
        logger.info("🔄 Обновление кэша: парсим данные с FFC API...")
        
        # Парсим свежие данные
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
        
        # Обновляем кэш
        self._cache['data'] = results
        self._cache['timestamp'] = time()
        
        logger.info(f"✅ Кэш обновлен. Найдено слотов: {sum(v['count'] for v in results.values())}")
        return results

    def get_cache_info(self) -> Dict:
        """Получаем информацию о кэше для отображения в примечании"""
        from time import time
        current_time = time()
        
        if not self._cache['timestamp']:
            return {
                'is_fresh': False,
                'last_update': None,
                'is_cached': False
            }
        
        cache_age = current_time - self._cache['timestamp']
        is_fresh = cache_age < self._cache['ttl']
        
        # Время последнего обновления в московском часовом поясе
        last_update_dt = datetime.fromtimestamp(self._cache['timestamp'], MOSCOW_TZ)
        
        return {
            'is_fresh': is_fresh,
            'last_update': last_update_dt.strftime("%H:%M"),
            'is_cached': self._cache['data'] is not None,
            'current_time': datetime.now(MOSCOW_TZ).strftime("%H:%M")
        }

# ===================== СОЗДАЕМ ПАРСЕР =====================
parser = FFCParser()
TOKEN = os.environ.get("BOT_TOKEN")

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
        "• Будни (Пн-Пт) — слоты с 18:30 до 21:00\n"
        "• Выходные — слоты с 08:30 до 21:30\n\n"
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
        "2. *Будни (Пн-Пт):* слоты с 18:30 до 21:00\n"
        "3. *Выходные:* слоты с 08:30 до 21:30\n"
        "4. Данные обновляются автоматически\n\n"
        "❓ Есть вопросы? Обращайтесь к разработчику!"
    )
    await update.message.reply_text(text, parse_mode='Markdown')

async def slots_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /slots — ГЛАВНАЯ ФУНКЦИЯ"""
    # Получаем текущее московское время для отображения
    current_time_moscow = datetime.now(MOSCOW_TZ)
    current_time_str = current_time_moscow.strftime("%H:%M")
    
    # Отправляем сообщение о начале поиска
    message = await update.message.reply_text(
        f"🔍 *Ищу свободные слоты...*\n"
        f"_Запрос отправлен в {current_time_str} ⏳_",
        parse_mode='Markdown'
    )
    
    try:
        # Получаем все слоты
        results = parser.get_all_venues_slots()
        
        # Получаем информацию о кэше
        cache_info = parser.get_cache_info()
        
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
                
                # СОЗДАЕМ КОРРЕКТНОЕ ПРИМЕЧАНИЕ С АКТУАЛЬНЫМ ВРЕМЕНЕМ
                now_moscow = datetime.now(MOSCOW_TZ)
                time_str = now_moscow.strftime("%H:%M")
                date_str = now_moscow.strftime("%d.%m.%Y")
                
                # Определяем источник данных
                if cache_info['is_cached'] and cache_info['is_fresh']:
                    data_source = "кэшированные данные"
                else:
                    data_source = "актуальные данные"
                
                footer = (
                    f"\n📝 *Примечание:*\n"
                    f"• Данные актуальны на {time_str} ({date_str})\n"
                    f"• Будни (Пн-Пт): показываются слоты с 18:30 до 21:00\n"
                    f"• Выходные: показываются слоты с 08:30 до 21:30\n"
                    f"• Данные обновляются каждые 5 минут"
                )
                
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
    logger.info(f"🌐 Часовой пояс: {MOSCOW_TZ}")
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
