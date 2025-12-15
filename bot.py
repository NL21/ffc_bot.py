"""
УНИВЕРСАЛЬНЫЙ ПАРСЕР FFC.TEAM - С ФИЛЬТРАЦИЕЙ ПО ВРЕМЕНИ
"""

import requests
import json
from datetime import datetime, timedelta
from typing import Dict, List, Set, Optional

class UniversalFFCParser:
    """Главный класс парсера"""
    
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Content-Type": "application/json",
        }
    
    def get_period(self) -> tuple:
        """Рассчитываем период поиска"""
        today = datetime.now()
        weekday = today.weekday()
        days_to_sunday = 6 - weekday
        total_days = days_to_sunday + 7
        return today, total_days
    
    def fetch_raw_slots(self, venue_id: str, date_str: str) -> List[Dict]:
        """Получаем сырые данные с API"""
        api_url = f"https://api.vivacrm.ru/end-user/api/v1/iSkq6G/products/master-services/{venue_id}/timeslots"
        
        payload = {
            "date": date_str,
            "trainers": {"type": "NO_TRAINER"}
        }
        
        try:
            response = requests.post(api_url, json=payload, headers=self.headers, timeout=10)
            data = response.json()
            return data.get("byTrainer", {}).get("NO_TRAINER", {}).get("slots", [])
        except Exception as e:
            print(f"⚠️ Ошибка для {date_str}: {e}")
            return []
    
    def parse_slots(self, venue_id: str) -> List[Dict]:
        """Основной метод парсинга"""
        start_date, total_days = self.get_period()
        
        all_raw_slots = []
        
        # Собираем данные за весь период
        for day_offset in range(total_days + 1):
            current_date = start_date + timedelta(days=day_offset)
            date_str = current_date.strftime("%Y-%m-%d")
            
            raw_slots = self.fetch_raw_slots(venue_id, date_str)
            
            # Обрабатываем каждый слот
            for slot_group in raw_slots:
                for slot in slot_group:
                    try:
                        time_from = slot.get("timeFrom", "")
                        time_to = slot.get("timeTo", "")
                        available_duration = slot.get("availableDuration", "PT30M")
                        
                        dt_from = datetime.fromisoformat(time_from.replace('Z', '+00:00'))
                        dt_to = datetime.fromisoformat(time_to.replace('Z', '+00:00'))
                        
                        all_raw_slots.append({
                            'datetime': dt_from,
                            'date': dt_from.strftime("%d.%m.%Y"),
                            'weekday': ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"][dt_from.weekday()],
                            'weekday_num': dt_from.weekday(),  # 0=пн, 6=вс
                            'start': dt_from.strftime("%H:%M"),
                            'end': dt_to.strftime("%H:%M"),
                            'start_hour': int(dt_from.strftime("%H")),
                            'start_minute': int(dt_from.strftime("%M")),
                            'time': f"{dt_from.strftime('%H:%M')}-{dt_to.strftime('%H:%M')}",
                            'room': slot.get("roomName", ""),
                            'price': slot.get("price", {}).get("from", 0),
                            'duration': available_duration,
                            'duration_minutes': self.duration_to_minutes(available_duration),
                            'unique_key': f"{dt_from.strftime('%Y%m%d%H%M')}"
                        })
                    except Exception:
                        continue
        
        # Правильная фильтрация слотов
        return self.smart_filter_slots(all_raw_slots)
    
    def duration_to_minutes(self, duration_str: str) -> int:
        """Преобразует PT1H30M в 90 минут"""
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
    
    def smart_filter_slots(self, slots: List[Dict]) -> List[Dict]:
        """
        УМНАЯ ФИЛЬТРАЦИЯ с учетом времени начала
        
        Правила:
        1. Слоты с duration = PT30M НЕ показываем, если они идут сразу после слота с duration > PT30M
        2. Слоты с duration > PT30M показываем ВСЕ
        3. Слоты с duration = PT30M показываем, если они идут после другого слота с PT30M
        4. Дополнительная фильтрация по времени начала:
           - В будни (пн-пт): показываем только слоты, начинающиеся с 18:30 и позже
           - В выходные (сб-вс): показываем все слоты
        """
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
        
        # 3. Применяем правила фильтрации по availableDuration
        filtered_by_duration = []
        i = 0
        n = len(unique_slots)
        
        while i < n:
            current_slot = unique_slots[i]
            
            # Проверяем следующий слот
            if i + 1 < n:
                next_slot = unique_slots[i + 1]
                
                # Если следующий слот в тот же день и время непрерывное
                if (next_slot['date'] == current_slot['date'] and 
                    next_slot['start'] == current_slot['end']):
                    
                    # Если текущий слот имеет длительность > 30 мин, а следующий = 30 мин
                    if (current_slot['duration_minutes'] > 30 and 
                        next_slot['duration_minutes'] == 30):
                        # Пропускаем следующий слот (это продолжение)
                        i += 1
                
                # Если оба слота имеют длительность > 30 мин и идут подряд
                elif (next_slot['date'] == current_slot['date'] and 
                      next_slot['start'] == current_slot['end'] and
                      current_slot['duration_minutes'] > 30 and
                      next_slot['duration_minutes'] > 30):
                    # Показываем оба (как 19:00-19:30 и 19:30-20:00)
                    pass
            
            # Добавляем текущий слот
            filtered_by_duration.append(current_slot)
            i += 1
        
        # 4. Применяем фильтрацию по времени начала (будни/выходные)
        final_slots = []
        for slot in filtered_by_duration:
            # Определяем, будний ли день (пн-пт = 0-4)
            is_weekday = slot['weekday_num'] < 5  # 0-4 = пн-пт
            
            if is_weekday:
                # Проверяем время начала для буднего дня
                start_time_str = slot['start']
                # Преобразуем "HH:MM" в количество минут с начала дня для сравнения
                hours, minutes = map(int, start_time_str.split(':'))
                total_minutes = hours * 60 + minutes
                
                # Сравниваем с 18:30 (18*60 + 30 = 1110 минут)
                if total_minutes >= 1110:  # 18:30 или позже
                    final_slots.append(slot)
                else:
                    # Слот раньше 18:30 в будний день - пропускаем
                    continue
            else:
                # Выходной день - показываем все слоты
                final_slots.append(slot)
        
        # 5. Форматируем результат
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

class FFCBotManager:
    """Менеджер для работы с несколькими площадками"""
    
    def __init__(self):
        self.parser = UniversalFFCParser()
        self.venues = self.load_venues()
    
    def load_venues(self) -> Dict:
        """Загружаем список площадок"""
        return {
            "seliger": {
                "id": "de503e35-1a81-430c-b919-c2e8fac638c2",
                "name": "Селигерская (Футбольный манеж)",
                "url": "https://ffc.team/rent_seliger"
            },
            "kantem": {
                "id": "9da0ba06-e433-43cd-b955-1981d0734b9f",
                "name": "Кантемировская",
                "url": "https://ffc.team/rent_kantem"
            },
        }
    
    def get_all_slots(self) -> Dict:
        """Получаем слоты для всех площадок"""
        results = {}
        
        print("\n📊 ПОЛУЧЕНИЕ ДАННЫХ...")
        
        for venue_key, venue_info in self.venues.items():
            print(f"🔍 {venue_info['name']}...")
            
            slots = self.parser.parse_slots(venue_info['id'])
            results[venue_key] = {
                'name': venue_info['name'],
                'slots': slots,
                'count': len(slots)
            }
            
            print(f"   Найдено слотов: {len(slots)}")
        
        return results
    
    def format_for_telegram(self, results: Dict) -> str:
        """Форматируем вывод"""
        if not results:
            return "❌ Не удалось получить данные."
        
        messages = []
        
        for venue_key, venue_data in results.items():
            slots = venue_data['slots']
            if not slots:
                continue
            
            venue_msg = f"\n🏟️ *{venue_data['name']}*\n"
            
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
            return "🎯 На ближайшие 2 недели свободных слотов не найдено."
        
        return "\n" + "="*40 + "\n".join(messages)
    
    def debug_venue_with_time_filter(self, venue_key: str, date_str: str = None):
        """
        Отладка с показом фильтрации по времени
        """
        if venue_key not in self.venues:
            print(f"❌ Площадка '{venue_key}' не найдена")
            return
        
        if not date_str:
            date_str = datetime.now().strftime("%Y-%m-%d")
        
        venue_info = self.venues[venue_key]
        print(f"\n🔧 ОТЛАДКА С ФИЛЬТРОМ ПО ВРЕМЕНИ: {venue_info['name']} на {date_str}")
        print("="*60)
        
        # Получаем сырые данные
        raw_slots = self.parser.fetch_raw_slots(venue_info['id'], date_str)
        
        if not raw_slots:
            print("   Нет данных")
            return
        
        print("📊 Все слоты от API с логикой фильтрации:")
        print("-"*60)
        
        all_slots = []
        for slot_group in raw_slots:
            for slot in slot_group:
                try:
                    time_from = slot.get("timeFrom", "")
                    time_to = slot.get("timeTo", "")
                    available_duration = slot.get("availableDuration", "PT30M")
                    
                    dt_from = datetime.fromisoformat(time_from.replace('Z', '+00:00'))
                    dt_to = datetime.fromisoformat(time_to.replace('Z', '+00:00'))
                    
                    weekday_num = dt_from.weekday()
                    weekday_name = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"][weekday_num]
                    start_time_str = dt_from.strftime("%H:%M")
                    
                    slot_info = {
                        'datetime': dt_from,
                        'date': dt_from.strftime("%d.%m.%Y"),
                        'weekday': weekday_name,
                        'weekday_num': weekday_num,
                        'start': start_time_str,
                        'end': dt_to.strftime("%H:%M"),
                        'time': f"{start_time_str}-{dt_to.strftime('%H:%M')}",
                        'duration': available_duration,
                        'duration_minutes': self.parser.duration_to_minutes(available_duration),
                        'price': slot.get("price", {}).get("from", 0),
                        'room': slot.get("roomName", "")
                    }
                    all_slots.append(slot_info)
                except Exception as e:
                    continue
        
        # Сортируем
        all_slots.sort(key=lambda x: (x['date'], x['start']))
        
        # Показываем логику
        for i, slot in enumerate(all_slots):
            show = "✓"  # По умолчанию показываем
            reason = ""
            
            # Проверяем логику фильтрации по duration
            if i > 0:
                prev_slot = all_slots[i - 1]
                if (slot['date'] == prev_slot['date'] and 
                    slot['start'] == prev_slot['end'] and
                    prev_slot['duration_minutes'] > 30 and
                    slot['duration_minutes'] == 30):
                    show = "✗"
                    reason = "(продолжение)"
            
            # Проверяем фильтрацию по времени (будни/выходные)
            if show == "✓":
                is_weekday = slot['weekday_num'] < 5  # пн-пт
                if is_weekday:
                    # Преобразуем время начала в минуты
                    hours, minutes = map(int, slot['start'].split(':'))
                    total_minutes = hours * 60 + minutes
                    
                    if total_minutes < 1110:  # раньше 18:30
                        show = "✗"
                        reason = f"(будни, до 18:30: {slot['start']})"
            
            print(f"{i+1:2d}. {slot['time']} | {slot['weekday']} | "
                  f"Длит: {slot['duration']} | Цена: {slot['price']} руб. | {show} {reason}")

def main():
    """Основная функция"""
    print("="*60)
    print("⚽ УНИВЕРСАЛЬНЫЙ ПАРСЕР FFC.TEAM - С ФИЛЬТРОМ ПО ВРЕМЕНИ")
    print("="*60)
    
    # Создаем менеджер
    manager = FFCBotManager()
    
    # ============================================
    # ВАРИАНТ 1: ОТЛАДКА С ФИЛЬТРОМ ПО ВРЕМЕНИ
    # ============================================
    # Раскомментируйте 3 строки ниже для отладки
    # print("\n🔧 ОТЛАДКА С ФИЛЬТРОМ ПО ВРЕМЕНИ")
    # manager.debug_venue_with_time_filter("kantem", "2025-12-15")
    # return
    
    # ============================================
    # ВАРИАНТ 2: ОСНОВНОЙ РЕЖИМ
    # ============================================
    print("\n🏟️ ДОСТУПНЫЕ ПЛОЩАДКИ:")
    for key, venue in manager.venues.items():
        print(f"  • {venue['name']} (ключ: {key})")
    
    results = manager.get_all_slots()
    output = manager.format_for_telegram(results)
    print(output)
    
    print("\n✅ Готово! Парсер завершил работу.")
    print("\n📝 ПРИМЕЧАНИЕ: В будни показываются только слоты с 18:30 и позже.")
    print("   В выходные показываются все свободные слоты.")

if __name__ == "__main__":
    main()