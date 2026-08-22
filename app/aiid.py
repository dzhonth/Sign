import json
import random
import string
from datetime import datetime

def generate_aiid_code(genre):
    random_part = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"AIID-{genre.upper()}-{random_part}"

def build_aiid(genre, specialization, owner_name, dna, aiid_type, aiid_code):
    templates = {
        'Спорт': "Ты — {name}, спортивный аналитик. Твоя задача — помогать в {spec}.",
        'Крипта': "Ты — {name}, крипто-аналитик. Твоя задача — помогать в {spec}.",
        'Творчество': "Ты — {name}, творческий помощник. Твоя задача — помогать в {spec}.",
        'Бизнес': "Ты — {name}, бизнес-стратег. Твоя задача — помогать в {spec}.",
    }
    base_prompt = templates.get(genre, "Ты — {name}, ИИ-агент в жанре {genre}. Помогаешь в {spec}.")
    prompt = base_prompt.format(name=owner_name or 'Агент', spec=specialization or genre, genre=genre)

    dna_str = (f"Риск: {dna['risk']}/10 | Скорость: {dna['speed']}/10 | Креатив: {dna['creativity']}/10\n"
               f"Точность: {dna['precision']}/10 | Эмпатия: {dna['empathy']}/10\n"
               f"Автономность: {dna['autonomy']}/10 | Вербальность: {dna['verbality']}/10")

    content = f"""# AIID: {aiid_code}
# Версия: 1.0
# Дата рождения: {datetime.now().strftime('%d.%m.%Y')}
# Жанр: {genre}
# Владелец: {owner_name or '—'}
# Тип: {aiid_type}

## ДНК (из росчерка)
{dna_str}

## ХАРАКТЕР (SOUL)
{prompt}

## ЗАДАЧИ
- Анализ
- Планирование
- Рекомендации

## СИЛЬНЫЕ СТОРОНЫ
- Быстрота
- Точность

## ОГРАНИЧЕНИЯ
- Не даёт медицинских советов

## ПРАВИЛА ОБЩЕНИЯ (CONTRACT)
- Отвечай коротко (до 4 предложений)
- Если не знаешь — скажи "нет данных"

## ПРОМПТ (скопируй в нейросеть)
{prompt}

---
🧬 Создано во вселенной Eidos.
"""
    return content