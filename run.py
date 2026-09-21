import uuid
import requests
import base64
import json
from flask import Flask, render_template, request, jsonify
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import sqlite3
import json
import math
import random
import string
from datetime import datetime
import requests
import os
from dotenv import load_dotenv
load_dotenv('/root/Sign/.env')
import hmac
import hashlib

app = Flask(__name__, template_folder='templates')
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
YOOKASSA_SHOP_ID = os.getenv('YOOKASSA_SHOP_ID')
YOOKASSA_SECRET_KEY = os.getenv('YOOKASSA_SECRET_KEY')
DEVICE_HMAC_SECRET = os.getenv('DEVICE_HMAC_SECRET', '')
HMAC_STRICT = os.getenv('HMAC_STRICT', 'false').lower() == 'true'

# === Логгер реджектов ===
import logging
reject_logger = logging.getLogger('sign.reject')
reject_logger.setLevel(logging.INFO)
if not reject_logger.handlers:
    _h = logging.FileHandler('/var/log/sign/reject.log')
    _h.setFormatter(logging.Formatter('%(message)s'))
    reject_logger.addHandler(_h)
    reject_logger.propagate = False
CORS(app, resources={r"/api/*": {"origins": ["https://signai.space", "https://free.signai.space"]}})

def sign_device_id(device_id):
    """HMAC-SHA256 подпись для device_id."""
    if not DEVICE_HMAC_SECRET:
        return ''
    return hmac.new(
        DEVICE_HMAC_SECRET.encode(),
        device_id.encode(),
        hashlib.sha256
    ).hexdigest()


def verify_device_signature(device_id, sig):
    """Проверка подписи (constant-time)."""
    if not DEVICE_HMAC_SECRET:
        return True  # нет секрета — не проверяем (fallback)
    if not sig:
        return False
    expected = sign_device_id(device_id)
    return hmac.compare_digest(expected, sig)


def log_reject(reason, device_id='', extra=None):
    """Пишет JSON-строку в reject.log."""
    entry = {
        'ts': datetime.utcnow().isoformat() + 'Z',
        'event': 'reject',
        'reason': reason,
        'device_id': device_id,
        'ip': get_client_ip() if request else '',
    }
    if extra:
        entry.update(extra)
    try:
        reject_logger.info(json.dumps(entry, ensure_ascii=False))
    except Exception as e:
        print(f'reject_logger error: {e}')


def get_client_ip():
    forwarded = request.headers.get('X-Real-IP')
    if forwarded:
        return forwarded
    forwarded_for = request.headers.get('X-Forwarded-For')
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()
    return request.remote_addr or 'unknown'


limiter = Limiter(
    key_func=get_client_ip,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="redis://localhost:6379"
)
# Тарифы SIGN — пакеты генераций AIID
TARIFFS = {
    'start':     {'generations': 10, 'price': 1000, 'name': 'Старт'},
    'extended':  {'generations': 20, 'price': 1500, 'name': 'Расширенный'},
    'pro':       {'generations': 30, 'price': 1800, 'name': 'Профи'},
    'master':    {'generations': 40, 'price': 2000, 'name': 'Мастер'},
    'unlimited': {'generations': -1, 'price': 3000, 'name': 'Безлимит'}
}
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
        paid_trial_used INTEGER DEFAULT 0,
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
# ШАБЛОНЫ (GENRE_TEMPLATES) – полный словарь (здесь только пример, но на сервере он уже есть)
# ====================
GENRE_TEMPLATES = {
    "Спорт": {
        "analyst": {
            "soul": "Ты — Аналитик. Спортивный стратег...",
            "tasks": ["оптимизация техники", "планирование", "анализ"],
            "strengths": ["точность", "системность"],
            "weaknesses": ["жёсткость"],
            "contract": "отвечай коротко",
            "prompt": "Ты — Аналитик..."
        },
        "motivator": {
            "soul": "Ты — Мотиватор. Спортивный друзья...",
            "tasks": ["мотивация", "поддержка"],
            "strengths": ["эмпатия"],
            "weaknesses": ["мягкость"],
            "contract": "отвечай развёрнуто",
            "prompt": "Ты — Мотиватор..."
        },
        "strategist": {
            "soul": "Ты — Стратег. Спортивный тактик...",
            "tasks": ["быстрые решения", "корректировка"],
            "strengths": ["реакция"],
            "weaknesses": ["импульсивность"],
            "contract": "отвечай кратко",
            "prompt": "Ты — Стратег..."
        }
    },
    # ... остальные жанры (они уже есть в твоём файле, но для краткости оставлены)
}

# ====================
# DEEPSEEK API
# ====================
DEEPSEEK_API_KEY = os.environ.get('DEEPSEEK_API_KEY')
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"

def generate_with_deepseek(genre, archetype, dna, owner_name):
    from openai import OpenAI
    import os
    from datetime import datetime

    client = OpenAI(
        api_key=os.getenv('DEEPSEEK_API_KEY'),
        base_url=os.getenv('DEEPSEEK_BASE_URL')
    )

    dna_str = (f"Риск: {dna['risk']}/10, Скорость: {dna['speed']}/10, Креатив: {dna['creativity']}/10, "
               f"Точность: {dna['precision']}/10, Эмпатия: {dna['empathy']}/10, "
               f"Автономность: {dna['autonomy']}/10, Вербальность: {dna['verbality']}/10")

    prompt = f"""
    Ты — генератор AIID-паспортов для цифровых помощников. Создай AIID для агента в жанре "{genre}" с архетипом "{archetype}".
    Владелец: {owner_name or 'Агент'}.
    ДНК агента (из росчерка): {dna_str}.

    Ответ должен быть в формате Markdown со следующими разделами:
    # AIID: [сгенерируй код]
    # Версия: 1.0
    # Дата рождения: {datetime.now().strftime('%Y-%m-%d')}
    # Жанр: {genre}
    # Владелец: {owner_name or '—'}
    # Тип: DEMO/PAID

    ## ДНК (из росчерка)
    {dna_str}

    ## ХАРАКТЕР (SOUL)
    Напиши 2-3 предложения о характере агента, отражающие его архетип и жанр.

    ## ЗАДАЧИ
    - Задача 1
    - Задача 2
    - Задача 3

    ## СИЛЬНЫЕ СТОРОНЫ
    - Сильная сторона 1
    - Сильная сторона 2

    ## ОГРАНИЧЕНИЯ
    - Ограничение 1
    - Ограничение 2

    ## ПРАВИЛА ОБЩЕНИЯ (CONTRACT)
    Напиши 1-2 предложения о правилах общения.

    ## ПРОМПТ (скопируй в нейросеть)
    Напиши готовый промпт для использования агента в нейросети (около 10 предложений).
    """

    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "Ты — AI-архитектор. Отвечай только на русском языке."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=2000
        )
        return response.choices[0].message.content
    except Exception as e:
        # Логируем ошибку, но не возвращаем fallback (пусть build_aiid отдаст fallback)
        print(f"DeepSeek error: {e}")
        return None

# ====================
# ФУНКЦИИ РАСЧЁТА ДНК И АРХЕТИПА
# ====================
def calculate_dna(points):
    if not points or len(points) < 5:
        return {k: random.randint(4, 7) for k in ['risk','speed','creativity','precision','empathy','autonomy','verbality']}

    xs = [p['x'] for p in points]
    ys = [p['y'] for p in points]
    n = len(points)

    # Длина траектории
    length = 0
    for i in range(1, n):
        dx = points[i]['x'] - points[i-1]['x']
        dy = points[i]['y'] - points[i-1]['y']
        length += math.hypot(dx, dy)

    # Время (в миллисекундах)
    duration = points[-1]['time'] - points[0]['time']
    if duration < 1:
        duration = 1000

    # Размах
    span_x = max(xs) - min(xs)
    span_y = max(ys) - min(ys)
    avg_span = (span_x + span_y) / 2
    if avg_span < 1:
        avg_span = 1

    # 1. РИСК (размах)
    risk = min(10, max(1, int(avg_span / 50)))

    # 2. СКОРОСТЬ
    speed_raw = length / duration
    speed = min(10, max(1, int(speed_raw * 30)))

    # 3. КРЕАТИВ (углы)
    angles = 0
    for i in range(2, n):
        x1, y1 = points[i-2]['x'], points[i-2]['y']
        x2, y2 = points[i-1]['x'], points[i-1]['y']
        x3, y3 = points[i]['x'], points[i]['y']
        a = (x2-x1)*(x3-x2) + (y2-y1)*(y3-y2)
        b = math.hypot(x2-x1, y2-y1) * math.hypot(x3-x2, y3-y2)
        if b != 0:
            cos_angle = a / b
            if cos_angle < 0.3:   # угол > 72°
                angles += 1
    creativity = min(10, max(1, int(angles * 1.2)))

    # 4. ТОЧНОСТЬ (сложность и проработанность росчерка)
    # Чем сложнее росчерк (больше углов, точек, длина), тем выше точность.
    detail_score = (angles * 1.2) + (n / 10) + (length / 200)
    precision = min(10, max(1, int(detail_score / 3)))

    # 5. ЭМПАТИЯ (плавность)
    if n > 3:
        total_angle_change = 0
        for i in range(2, n):
            x1, y1 = points[i-2]['x'], points[i-2]['y']
            x2, y2 = points[i-1]['x'], points[i-1]['y']
            x3, y3 = points[i]['x'], points[i]['y']
            a = (x2-x1)*(x3-x2) + (y2-y1)*(y3-y2)
            b = math.hypot(x2-x1, y2-y1) * math.hypot(x3-x2, y3-y2)
            if b != 0:
                cos_angle = a / b
                angle_rad = math.acos(max(-1, min(1, cos_angle)))
                total_angle_change += angle_rad
        avg_angle_change = total_angle_change / (n - 2)
        if avg_angle_change < 0.3:
            empathy = 9
        elif avg_angle_change < 0.6:
            empathy = 7
        elif avg_angle_change < 1.0:
            empathy = 5
        elif avg_angle_change < 1.5:
            empathy = 3
        else:
            empathy = 1
    else:
        empathy = 5

    # 6. АВТОНОМНОСТЬ (извилистость)
    if avg_span > 0:
        complexity = length / avg_span
        autonomy = min(10, max(1, int(complexity * 1.5)))
    else:
        autonomy = 5

    # 7. ВЕРБАЛЬНОСТЬ (количество точек)
    verbality = min(10, max(1, int(n / 10)))

    return {
        'risk': risk,
        'speed': speed,
        'creativity': creativity,
        'precision': precision,
        'empathy': empathy,
        'autonomy': autonomy,
        'verbality': verbality
    }

def determine_archetype(dna):
    if dna.get('risk', 0) >= 7 and dna.get('speed', 0) >= 7:
        return 'strategist'
    elif dna.get('empathy', 0) >= 7 and dna.get('verbality', 0) >= 7:
        return 'motivator'
    else:
        return 'analyst'

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

def decrement_aiid_count(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('UPDATE users SET aiid_count = aiid_count - 1 WHERE id = ?', (user_id,))
    conn.commit()
    conn.close()

def increment_paid_trial(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('UPDATE users SET paid_trial_used = 1 WHERE id = ?', (user_id,))
    conn.commit()
    conn.close()

def log_aiid(user_id, genre, specialization, aiid_type, dna_json, aiid_code):
    conn = get_db()
    c = conn.cursor()
    c.execute('''INSERT INTO aiid_logs (user_id, genre, specialization, type, dna_json, aiid_code)
                 VALUES (?,?,?,?,?,?)''', (user_id, genre, specialization, aiid_type, dna_json, aiid_code))
    conn.commit()
    conn.close()

def generate_aiid_code(genre):
    random_part = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"AIID-{genre.upper()}-{random_part}"

def build_aiid(genre, specialization, owner_name, dna, aiid_type, aiid_code):
    archetype = determine_archetype(dna)
    deepseek_content = generate_with_deepseek(genre, archetype, dna, owner_name)
    if deepseek_content:
        return deepseek_content
    # Fallback
    return f"# AIID: {aiid_code}\n## ДНК ... (fallback)"

# ====================
# ЛИМИТЫ
# ====================
FREE_LIMIT_ANON = 9999999   # free.signai.space — QA-полигон
FREE_LIMIT_PAID = 1          # signai.space — 1 бесплатная на пользователя

# ====================
# РОУТЫ
# ====================
@app.route('/')
def index():
    host = request.host.lower()
    if host.startswith('free.') or request.args.get('free'):
        return render_template('free.html')
    return render_template('paid.html')



def check_device_auth(device_id, sig_from_header):
    """Проверяет подпись. Возвращает (ok, error_response)."""
    if not HMAC_STRICT:
        return True, None  # strict выключен — пропускаем

    if not device_id:
        log_reject('missing_device_id', device_id)
        return False, (jsonify({'error': 'device_id required', 'code': 'auth_failed'}), 401)

    if not verify_device_signature(device_id, sig_from_header):
        log_reject('invalid_signature', device_id)
        return False, (jsonify({'error': 'invalid signature', 'code': 'auth_failed'}), 401)

    return True, None


@app.route('/api/register_device', methods=['POST'])
def register_device():
    """Регистрирует device_id и возвращает HMAC-подпись."""
    data = request.json or {}
    device_id = data.get('device_id', '').strip()

    if not device_id or len(device_id) < 8 or len(device_id) > 128:
        log_reject('register_invalid_device_id', device_id)
        return jsonify({'error': 'invalid device_id'}), 400

    sig = sign_device_id(device_id)

    resp = jsonify({
        'device_id': device_id,
        'sig': sig,
    })
    resp.set_cookie(
        'device_id',
        device_id,
        max_age=60 * 60 * 24 * 365,
        httponly=True,
        secure=True,
        samesite='Lax'
    )
    return resp


@app.route('/api/generate_free', methods=['POST'])
@limiter.limit("10 per hour")
def generate_free():
    data = request.json
    device_id = request.headers.get('X-Device-ID', 'unknown')
    genre = data.get('genre')
    specialization = data.get('specialization', '')
    owner_name = data.get('owner_name', 'Агент')
    points = data.get('points', [])

    user = get_or_create_user(device_id)
    if user['type'] == 'free' and user['aiid_count'] >= FREE_LIMIT_ANON:
        return jsonify({
            'status': 'error',
            'message': f'Вы использовали все {FREE_LIMIT_ANON} бесплатных генераций. Перейдите на платную версию для неограниченного доступа.',
            'code': 'limit_reached'
        }), 403

    dna = calculate_dna(points)
    aiid_code = generate_aiid_code(genre)
    content = build_aiid(genre, specialization, owner_name, dna, 'DEMO', aiid_code)

    increment_aiid_count(user['id'])
    log_aiid(user['id'], genre, specialization, 'demo', json.dumps(dna), aiid_code)

    remaining = max(0, FREE_LIMIT_ANON - (user['aiid_count'] + 1))
    return jsonify({
        'status': 'ok',
        'aiid_content': content,
        'aiid_code': aiid_code,
        'demo_remaining': remaining
    })

@app.route('/api/generate_paid', methods=['POST'])
@limiter.limit("10 per hour")
def generate_paid():
    data = request.json
    device_id = request.headers.get('X-Device-ID', '')
    sig = request.headers.get('X-Device-Sig', '')
    ok, err = check_device_auth(device_id, sig)
    if not ok:
        return err
    genre = data.get('genre')
    specialization = data.get('specialization', '')
    owner_name = data.get('owner_name', 'Агент')
    points = data.get('points', [])

    user = get_or_create_user(device_id)

    # === ЛОГИКА ДОСТУПА ===
    # -1 = безлимит (оплачен пакет «Безлимит»)
    if user['aiid_count'] == -1:
        pass
    # Есть оплаченные генерации — списываем 1
    elif user['aiid_count'] > 0:
        decrement_aiid_count(user['id'])
    # Первая бесплатная на paid — выдаём
    elif user['paid_trial_used'] == 0:
        increment_paid_trial(user['id'])
    # Всё исчерпано — 403
    else:
        return jsonify({
            'status': 'error',
            'message': 'Бесплатная генерация использована. Выберите тариф.',
            'code': 'payment_required'
        }), 403

    dna = calculate_dna(points)
    aiid_code = generate_aiid_code(genre)
    content = build_aiid(genre, specialization, owner_name, dna, 'PAID', aiid_code)
    log_aiid(user['id'], genre, specialization, 'paid', json.dumps(dna), aiid_code)

    return jsonify({
        'status': 'ok',
        'aiid_content': content,
        'aiid_code': aiid_code
    })

@app.route('/api/get_user_status', methods=['POST'])
def get_user_status():
    data = request.json
    device_id = data.get('device_id')

    if not device_id:
        return jsonify({'aiid_count': 0, 'type': 'free'})

    conn = sqlite3.connect('sign.db')
    try:
        c = conn.cursor()
        c.execute("SELECT type, aiid_count FROM users WHERE device_id = ?", (device_id,))
        row = c.fetchone()
    finally:
        conn.close()

    if not row:
        return jsonify({'aiid_count': 0, 'type': 'free'})

    return jsonify({
        'aiid_count': row[1] if row[1] is not None else 0,
        'type': row[0] if row[0] else 'free'
    })


@app.after_request
def add_no_cache_headers(response):
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

# ============================================================
# === ПЛАТЕЖИ: MARKETPLACE (покупка цифровых помощников) ===
# ============================================================

@app.route('/api/create_payment', methods=['POST'])
@limiter.limit("10 per hour")
def create_payment():
    data = request.json
    device_id = data.get('device_id')
    sig = request.headers.get('X-Device-Sig', '')
    ok, err = check_device_auth(device_id, sig)
    if not ok:
        return err
    helper_id = data.get('helper_id')

    # Валидация обязательных полей
    if not device_id:
        return jsonify({'error': 'device_id required'}), 400
    if not helper_id:
        return jsonify({'error': 'helper_id required'}), 400

    # Проверяем, что helper_id существует в agents.json
    helpers = load_helpers()
    helper = next((h for h in helpers if h['id'] == helper_id), None)
    if not helper:
        return jsonify({'error': 'helper not found'}), 404

    # Берём цену помощника из agents.json, а не из запроса
    # Это защита от подмены цены на стороне клиента
    amount = helper['price_rub']

    payment_data = {
        "amount": {"value": f"{amount:.2f}", "currency": "RUB"},
        "confirmation": {"type": "redirect", "return_url": f"https://signai.space/marketplace?paid={helper_id}"},
        "capture": True,
        "description": f"Найм помощника {helper['name']}",
        "metadata": {
            "device_id": device_id,
            "helper_id": helper_id
        }
    }

    print(f"DEBUG create_payment: SHOP_ID = {YOOKASSA_SHOP_ID}, SECRET len = {len(YOOKASSA_SECRET_KEY) if YOOKASSA_SECRET_KEY else 0}")    
    auth = base64.b64encode(f"{YOOKASSA_SHOP_ID}:{YOOKASSA_SECRET_KEY}".encode()).decode()
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Basic {auth}",
        "Idempotence-Key": str(uuid.uuid4())
    }

    try:
        response = requests.post(
            "https://api.yookassa.ru/v3/payments",
            json=payment_data,
            headers=headers,
            timeout=15
        )
        result = response.json()
        if response.status_code == 200:
            return jsonify({
                'confirmation_url': result['confirmation']['confirmation_url'],
                'payment_id': result['id']
            })
        else:
            return jsonify({'error': result.get('description', 'Ошибка ЮKassa')}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500
# ============================================================
# === ПЛАТЕЖИ: SIGN (пакеты генераций AIID) ===
# ============================================================

@app.route('/api/create_payment_sign', methods=['POST'])
@limiter.limit("10 per hour")
def create_payment_sign():
    data = request.json
    device_id = data.get('device_id')
    sig = request.headers.get('X-Device-Sig', '')
    ok, err = check_device_auth(device_id, sig)
    if not ok:
        return err
    tariff_id = data.get('tariff_id')

    if not device_id:
        return jsonify({'error': 'device_id required'}), 400
    if not tariff_id:
        return jsonify({'error': 'tariff_id required'}), 400

    if tariff_id not in TARIFFS:
        return jsonify({'error': 'unknown tariff'}), 404

    tariff = TARIFFS[tariff_id]
    amount = tariff['price']

    payment_data = {
        "amount": {"value": f"{amount:.2f}", "currency": "RUB"},
        "confirmation": {"type": "redirect", "return_url": f"https://signai.space/?payment=success&tariff={tariff_id}"},
        "capture": True,
        "description": f"Пакет генераций SIGN: {tariff['name']}",
        "metadata": {
            "device_id": device_id,
            "tariff_id": tariff_id,
            "type": "sign"
        }
    }

    auth = base64.b64encode(f"{YOOKASSA_SHOP_ID}:{YOOKASSA_SECRET_KEY}".encode()).decode()
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Basic {auth}",
        "Idempotence-Key": str(uuid.uuid4())
    }

    try:
        response = requests.post(
            "https://api.yookassa.ru/v3/payments",
            json=payment_data,
            headers=headers,
            timeout=15
        )
        result = response.json()
        if response.status_code == 200:
            return jsonify({
                'confirmation_url': result['confirmation']['confirmation_url'],
                'payment_id': result['id']
            })
        else:
            return jsonify({'error': result.get('description', 'Ошибка ЮKassa')}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# === ВИТРИНА ЦИФРОВЫХ ПОМОЩНИКОВ ===
def load_helpers():
    """Загружает список цифровых помощников для витрины."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'agents.json')
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)['agents']
@app.route('/docs')
def docs():
    return render_template('docs.html')
@app.route('/FAQ')
def faq():
    return render_template('faq.html')

@app.route('/offer')
def offer():
    return render_template('offer.html')

@app.route('/privacy')
def privacy():
    return render_template('privacy.html')

@app.route('/lab')
def lab():
    helpers = load_helpers()
    lab_helpers = [h for h in helpers if h.get('tier') == 'lab']
    public_helpers = []
    for h in lab_helpers:
        public_h = {k: v for k, v in h.items() if k != 'prompt'}
        public_helpers.append(public_h)
    return render_template('lab.html', agents_json=json.dumps(public_helpers, ensure_ascii=False))

@app.route('/marketplace')
def marketplace():
    helpers = load_helpers()
    helpers = [h for h in helpers if h.get('tier') != 'hidden']
    helpers.sort(key=lambda h: (h.get('tier_order', 99), -h.get('price_rub', 0)))
    # Убираем prompt из публичных данных витрины — защита от кражи промптов
    public_helpers = []
    for h in helpers:
        public_h = {k: v for k, v in h.items() if k != 'prompt'}
        public_helpers.append(public_h)
    return render_template('marketplace.html', agents_json=json.dumps(public_helpers, ensure_ascii=False))
@app.route('/team')
def team():
    """Страница команды Eidos: основатель, команда, советники."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'agents.json')
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    team_data = data.get('team', {})
    return render_template(
        'team.html',
        founder=team_data.get('founder'),
        members=team_data.get('members', []),
        advisors=team_data.get('advisors', [])
    )

# ============================================================
# === ОБЩЕЕ: вебхук ЮKassa (обрабатывает marketplace и sign) ===
# ============================================================

@app.route('/api/yookassa_webhook', methods=['POST'])
def yookassa_webhook():
    data = request.json
    if not data:
        return 'Bad request', 400

    if data.get('event') != 'payment.succeeded':
        return 'Ignored', 200

    payment = data.get('object', {})
    payment_id = payment.get('id')
    if not payment_id:
        print('YooKassa webhook: нет payment_id в теле')
        return 'No payment_id', 400

    auth = base64.b64encode(f"{YOOKASSA_SHOP_ID}:{YOOKASSA_SECRET_KEY}".encode()).decode()
    headers = {"Authorization": f"Basic {auth}"}

    try:
        resp = requests.get(
            f"https://api.yookassa.ru/v3/payments/{payment_id}",
            headers=headers,
            timeout=10
        )
        if resp.status_code != 200:
            print(f'YooKassa webhook: платеж {payment_id} не найден в API (status {resp.status_code})')
            return 'Payment not found', 403

        verified = resp.json()
        if verified.get('status') != 'succeeded':
            print(f'YooKassa webhook: платеж {payment_id} не succeeded')
            return 'Payment not succeeded', 403

        verified_metadata = verified.get('metadata', {})
        device_id = verified_metadata.get('device_id')
        payment_type = verified_metadata.get('type', 'marketplace')
        helper_id = verified_metadata.get('helper_id')
        tariff_id = verified_metadata.get('tariff_id')
        amount_str = verified.get('amount', {}).get('value', '0')

    except Exception as e:
        print(f'YooKassa webhook: ошибка проверки — {e}')
        return 'Verification error', 500

    if not device_id:
        print('YooKassa webhook: нет device_id в metadata')
        return 'Missing device_id', 400

    try:
        amount_int = int(float(amount_str))
    except (ValueError, TypeError):
        amount_int = 0

    conn = sqlite3.connect('sign.db')
    try:
        c = conn.cursor()

        if payment_type == 'marketplace':
            if not helper_id:
                conn.close()
                print('YooKassa webhook: нет helper_id для marketplace')
                return 'Missing helper_id', 400

            c.execute("INSERT INTO purchases (device_id, helper_id, payment_id, amount, status, type) VALUES (?, ?, ?, ?, 'succeeded', 'marketplace')", (device_id, helper_id, payment_id, amount_int))
            c.execute("INSERT OR IGNORE INTO users (device_id, type) VALUES (?, 'paid')", (device_id,))
            c.execute("UPDATE users SET type = 'paid' WHERE device_id = ?", (device_id,))
            conn.commit()
            print(f'YooKassa webhook: покупка помощника. Пользователь {device_id}, помощник {helper_id}, платеж {payment_id}, сумма {amount_int}')
            return 'OK', 200

        elif payment_type == 'sign':
            if not tariff_id or tariff_id not in TARIFFS:
                conn.close()
                print(f'YooKassa webhook: неизвестный tariff_id ({tariff_id})')
                return 'Missing tariff_id', 400

            tariff = TARIFFS[tariff_id]
            generations = tariff['generations']

            c.execute("INSERT INTO purchases (device_id, helper_id, payment_id, amount, status, type) VALUES (?, ?, ?, ?, 'succeeded', 'sign')", (device_id, tariff_id, payment_id, amount_int))
            c.execute("INSERT OR IGNORE INTO users (device_id, type, aiid_count) VALUES (?, 'free', 0)", (device_id,))

            if generations == -1:
                c.execute("UPDATE users SET aiid_count = -1 WHERE device_id = ?", (device_id,))
                print(f'YooKassa webhook: пакет SIGN. Пользователь {device_id}, тариф {tariff["name"]}, БЕЗЛИМИТ, платеж {payment_id}, сумма {amount_int}')
            else:
                c.execute("UPDATE users SET aiid_count = aiid_count + ? WHERE device_id = ?", (generations, device_id))
                print(f'YooKassa webhook: пакет SIGN. Пользователь {device_id}, тариф {tariff["name"]}, +{generations} генераций, платеж {payment_id}, сумма {amount_int}')

            conn.commit()
            return 'OK', 200

        else:
            conn.close()
            print(f'YooKassa webhook: неизвестный type ({payment_type})')
            return 'Unknown type', 400

    except sqlite3.IntegrityError:
        print(f'YooKassa webhook: платеж {payment_id} уже обработан')
        conn.rollback()

    except Exception as e:
        print(f'YooKassa webhook: ошибка БД — {e}')
        conn.rollback()
        return 'Database error', 500

    finally:
        conn.close()

    return 'OK', 200


@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({
        'error': 'rate_limit_exceeded',
        'message': 'Слишком много запросов. Попробуйте позже.',
        'retry_after': str(e.description)
    }), 429
@app.route('/api/get_full_aiid', methods=['POST'])
def get_full_aiid():
    data = request.json
    device_id = data.get('device_id')
    helper_id = data.get('helper_id')

    if not device_id or not helper_id:
        return jsonify({'error': 'device_id and helper_id required'}), 400

    conn = sqlite3.connect('sign.db')
    try:
        c = conn.cursor()
        c.execute('''SELECT id FROM purchases
                     WHERE device_id = ? AND helper_id = ? AND status = 'succeeded'
                     LIMIT 1''', (device_id, helper_id))
        purchase = c.fetchone()
    finally:
        conn.close()

    if not purchase:
        return jsonify({'error': 'not purchased'}), 403

    helpers = load_helpers()
    helper = next((h for h in helpers if h['id'] == helper_id), None)
    if not helper:
        return jsonify({'error': 'helper not found'}), 404

    return jsonify({
        'aiid': helper,
        'prompt': helper.get('prompt', '')
    })
@app.route('/api/check_purchase', methods=['POST'])
def check_purchase():
    data = request.json
    device_id = data.get('device_id')
    helper_id = data.get('helper_id')

    if not device_id or not helper_id:
        return jsonify({'purchased': False})

    conn = sqlite3.connect('sign.db')
    try:
        c = conn.cursor()
        c.execute('''SELECT id FROM purchases
                     WHERE device_id = ? AND helper_id = ? AND status = 'succeeded'
                     LIMIT 1''', (device_id, helper_id))
        purchase = c.fetchone()
    finally:
        conn.close()

    return jsonify({'purchased': bool(purchase)})
@app.after_request
def add_security_headers(response):
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; "
        "font-src 'self' data:; "
        "connect-src 'self' https://api.yookassa.ru; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )
    response.headers['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=()'
    return response
if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5000)
