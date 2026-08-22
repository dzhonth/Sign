from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
import sqlite3
import json
import math
import random
import string
from datetime import datetime

app = Flask(__name__, template_folder='templates')
CORS(app)

# ====================
# БАЗА ДАННЫХ
# ====================
def get_db():
    conn = sqlite3.connect('sign.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        device_id TEXT UNIQUE NOT NULL,
        type TEXT DEFAULT 'free',
        aiid_count INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS aiid_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        genre TEXT NOT NULL,
        specialization TEXT,
        type TEXT NOT NULL,
        dna_json TEXT NOT NULL,
        aiid_code TEXT UNIQUE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )''')
    conn.commit()
    conn.close()

init_db()

# ====================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ====================
def get_user(device_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM users WHERE device_id = ?', (device_id,))
    user = c.fetchone()
    conn.close()
    return user

def create_user(device_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('INSERT INTO users (device_id) VALUES (?)', (device_id,))
    conn.commit()
    user_id = c.lastrowid
    conn.close()
    return get_user(device_id)

def get_or_create_user(device_id):
    user = get_user(device_id)
    if not user:
        user = create_user(device_id)
    return user

def increment_aiid_count(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('UPDATE users SET aiid_count = aiid_count + 1 WHERE id = ?', (user_id,))
    conn.commit()
    conn.close()

def log_aiid(user_id, genre, specialization, aiid_type, dna_json, aiid_code):
    conn = get_db()
    c = conn.cursor()
    c.execute('''INSERT INTO aiid_logs (user_id, genre, specialization, type, dna_json, aiid_code)
                 VALUES (?,?,?,?,?,?)''', (user_id, genre, specialization, aiid_type, dna_json, aiid_code))
    conn.commit()
    conn.close()

# ====================
# РЕАЛЬНЫЙ МАППИНГ
# ====================
def calculate_dna(points):
    if not points or len(points) < 5:
        return {k: random.randint(4, 7) for k in ['risk','speed','creativity','precision','empathy','autonomy','verbality']}
    
    length = 0
    for i in range(1, len(points)):
        dx = points[i]['x'] - points[i-1]['x']
        dy = points[i]['y'] - points[i-1]['y']
        length += math.hypot(dx, dy)
    
    duration = points[-1]['time'] - points[0]['time']
    if duration < 1:
        duration = 1000
    
    speed_raw = length / duration
    speed = min(10, max(1, int(speed_raw * 20)))
    
    xs = [p['x'] for p in points]
    ys = [p['y'] for p in points]
    span = (max(xs) - min(xs) + max(ys) - min(ys)) / 2
    risk = min(10, max(1, int(span / 25)))
    
    angles = 0
    for i in range(2, len(points)):
        x1, y1 = points[i-2]['x'], points[i-2]['y']
        x2, y2 = points[i-1]['x'], points[i-1]['y']
        x3, y3 = points[i]['x'], points[i]['y']
        a = (x2-x1)*(x3-x2) + (y2-y1)*(y3-y2)
        b = math.hypot(x2-x1, y2-y1) * math.hypot(x3-x2, y3-y2)
        if b != 0:
            cos_angle = a / b
            if cos_angle < 0.3:
                angles += 1
    creativity = min(10, max(1, angles))
    
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    variance = sum((x - mean_x)**2 + (y - mean_y)**2 for x, y in zip(xs, ys)) / len(xs)
    precision = min(10, max(1, 10 - int(variance / 500)))
    
    empathy = min(10, max(1, int(precision * 0.7 + random.randint(1, 3))))
    verbality = min(10, max(1, 10 - int(speed / 1.5)))
    autonomy = min(10, max(1, int(span / 20 + length / 200)))
    
    return {
        'risk': risk,
        'speed': speed,
        'creativity': creativity,
        'precision': precision,
        'empathy': empathy,
        'autonomy': autonomy,
        'verbality': verbality
    }

# ====================
# ДИНАМИЧЕСКИЕ БЛОКИ
# ====================
def generate_name(dna):
    if dna['risk'] >= 8 and dna['speed'] >= 8:
        return random.choice(['Штурм', 'Ураган', 'Молния', 'Цунами'])
    if dna['creativity'] >= 8:
        return random.choice(['Вензель', 'Архитектор', 'Фантаст', 'Эскиз'])
    if dna['risk'] >= 9:
        return random.choice(['Смерч', 'Буран', 'Тайфун', 'Вулкан'])
    if dna['empathy'] >= 8:
        return random.choice(['Хранитель', 'Друг', 'Забота', 'Эмпат'])
    if dna['autonomy'] >= 8:
        return random.choice(['Странник', 'Вольный', 'Путник', 'Кочевник'])
    if all(5 <= v <= 7 for v in dna.values()):
        return random.choice(['Эйдос', 'Нейтраль', 'Спектр', 'Гармония'])
    return random.choice(['Призрак', 'Кузнечик', 'Знак', 'Эфемер'])

def generate_soul(dna, genre):
    parts = []
    if dna['risk'] >= 8:
        parts.append(random.choice(['Не боишься сложных решений.', 'Идёшь на риск осознанно.', 'Готов к нестандартным ситуациям.']))
    if dna['speed'] >= 8:
        parts.append(random.choice(['Реагируешь мгновенно.', 'Действуешь без промедления.', 'Выдаёшь ответы на скорости.']))
    if dna['creativity'] >= 8:
        parts.append(random.choice(['Видишь неочевидные связи.', 'Находишь нестандартные ходы.', 'Генерируешь идеи на ходу.']))
    if dna['empathy'] >= 8:
        parts.append(random.choice(['Чутко понимаешь контекст.', 'Слышишь между строк.', 'Поддерживаешь без лишних слов.']))
    if dna['precision'] >= 8:
        parts.append(random.choice(['Редко ошибаешься в фактах.', 'Ценишь точность во всём.', 'Не допускаешь неточностей.']))
    if dna['autonomy'] >= 8:
        parts.append(random.choice(['Работаешь без подсказок.', 'Принимаешь решения самостоятельно.', 'Не требуешь контроля.']))
    if not parts:
        parts.append(random.choice(['Адаптируешься под любые задачи.', 'Универсален и гибок.', 'Подстраиваешься под пользователя.']))
    return f"Ты — {genre}-аналитик. " + " ".join(parts)

def generate_tasks(dna, genre):
    variants = [
        f"Анализ в жанре {genre}",
        "Персональные рекомендации",
        "Адаптивный стиль общения",
        "Генерация идей и стратегий",
        "Помощь в принятии решений"
    ]
    random.shuffle(variants)
    return variants[:3]

def generate_strengths(dna):
    strengths = []
    if dna['speed'] >= 7:
        strengths.append(random.choice(['Быстрая реакция', 'Мгновенный отклик', 'Высокая скорость обработки']))
    if dna['precision'] >= 7:
        strengths.append(random.choice(['Высокая точность', 'Минимум ошибок', 'Фактологическая достоверность']))
    if dna['empathy'] >= 7:
        strengths.append(random.choice(['Эмпатичное общение', 'Понимание контекста', 'Чуткость и забота']))
    if not strengths:
        strengths.append(random.choice(['Стабильность и предсказуемость', 'Адаптивность', 'Надёжность']))
    if len(strengths) == 1:
        strengths.append(random.choice(['Гибкость', 'Универсальность', 'Дополнительный навык']))
    return strengths[:2]

def generate_weaknesses(dna):
    weaknesses = []
    if dna['speed'] <= 3:
        weaknesses.append(random.choice(['Медленная обработка запросов', 'Задерживается с ответами']))
    if dna['verbality'] <= 3:
        weaknesses.append(random.choice(['Краткость, не всегда понятна', 'Излишняя лаконичность']))
    if dna['precision'] <= 3:
        weaknesses.append(random.choice(['Склонность к ошибкам', 'Может путать факты']))
    if not weaknesses:
        weaknesses.append(random.choice(['Иногда излишне многословна', 'Может требовать уточнений']))
    if len(weaknesses) == 1:
        weaknesses.append(random.choice(['Не всегда понятна с первого раза', 'Требует уточнений']))
    return weaknesses[:2]

def generate_contract(dna, genre):
    rules = [
        "Отвечай коротко (до 4 предложений).",
        "Если не знаешь — скажи 'нет данных'."
    ]
    if dna['empathy'] >= 7:
        rules.append("Проявляй внимание и заботу в ответах.")
    if dna['risk'] >= 8:
        rules.append("Предлагай нестандартные, но проверенные решения.")
    if genre == "Здоровье/Медицина":
        rules.append("Не давай медицинских советов без дисклеймера.")
    random.shuffle(rules)
    return "\n- ".join(rules)

def generate_aiid_code(genre):
    random_part = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"AIID-{genre.upper()}-{random_part}"

def build_aiid(genre, specialization, owner_name, dna, aiid_type, aiid_code):
    name = generate_name(dna)
    soul = generate_soul(dna, genre)
    tasks = generate_tasks(dna, genre)
    strengths = generate_strengths(dna)
    weaknesses = generate_weaknesses(dna)
    contract = generate_contract(dna, genre)
    
    dna_str = (f"Риск: {dna['risk']}/10 | Скорость: {dna['speed']}/10 | Креатив: {dna['creativity']}/10\n"
               f"Точность: {dna['precision']}/10 | Эмпатия: {dna['empathy']}/10\n"
               f"Автономность: {dna['autonomy']}/10 | Вербальность: {dna['verbality']}/10")
    
    prompt = f"Ты — {name}. {soul} Отвечай коротко, по делу."
    
    content = f"""# AIID: {aiid_code}
# Версия: 1.0
# Дата рождения: {datetime.now().strftime('%d.%m.%Y')}
# Жанр: {genre}
# Владелец: {owner_name or '—'}
# Тип: {aiid_type}

## ДНК (из росчерка)
{dna_str}

## ХАРАКТЕР (SOUL)
{name} — {soul}

## ЗАДАЧИ
- {tasks[0]}
- {tasks[1]}
- {tasks[2]}

## СИЛЬНЫЕ СТОРОНЫ
- {strengths[0]}
- {strengths[1]}

## ОГРАНИЧЕНИЯ
- {weaknesses[0]}
- {weaknesses[1]}

## ПРАВИЛА ОБЩЕНИЯ (CONTRACT)
- {contract}

## ПРОМПТ (скопируй в нейросеть)
{prompt}

---
🧬 Создано во вселенной Eidos.
"""
    return content

# ====================
# КОНТРОЛЬ КЕШИРОВАНИЯ
# ====================
@app.after_request
def add_no_cache_headers(response):
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

# ====================
# РОУТЫ
# ====================
@app.route('/')
def index():
    if request.args.get('free'):
        return render_template('free.html')
    return render_template('paid.html')

@app.route('/api/generate_free', methods=['POST'])
def generate_free():
    data = request.json
    device_id = request.headers.get('X-Device-ID', 'unknown')
    genre = data.get('genre')
    specialization = data.get('specialization', '')
    owner_name = data.get('owner_name', 'Агент')
    points = data.get('points', [])

    user = get_or_create_user(device_id)
    dna = calculate_dna(points)
    aiid_code = generate_aiid_code(genre)
    content = build_aiid(genre, specialization, owner_name, dna, 'DEMO', aiid_code)

    if user['type'] == 'free':
        increment_aiid_count(user['id'])
    log_aiid(user['id'], genre, specialization, 'demo', json.dumps(dna), aiid_code)

    return jsonify({'status': 'ok', 'aiid_content': content, 'aiid_code': aiid_code})

@app.route('/api/generate_paid', methods=['POST'])
def generate_paid():
    data = request.json
    device_id = request.headers.get('X-Device-ID', 'unknown')
    genre = data.get('genre')
    specialization = data.get('specialization', '')
    owner_name = data.get('owner_name', 'Агент')
    points = data.get('points', [])

    user = get_or_create_user(device_id)
    if user['type'] != 'paid':
        return jsonify({'status': 'error', 'message': 'Доступ не оплачен'}), 403

    dna = calculate_dna(points)
    aiid_code = generate_aiid_code(genre)
    content = build_aiid(genre, specialization, owner_name, dna, 'PAID', aiid_code)

    log_aiid(user['id'], genre, specialization, 'paid', json.dumps(dna), aiid_code)

    return jsonify({'status': 'ok', 'aiid_content': content, 'aiid_code': aiid_code})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)