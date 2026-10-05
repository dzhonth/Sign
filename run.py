import uuid
import requests
import base64
import json
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from functools import wraps
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
app.secret_key = os.getenv('SECRET_KEY') or os.getenv('STAFF_PASSWORD') or 'fallback-change-me'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = 60 * 60 * 24 * 7  # 7 дней
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

# === STAFF ROUTES ===
def staff_required(f):
    """Декоратор: доступ только для авторизованных через STAFF_PASSWORD."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get('staff_authenticated'):
            return redirect('/staff/login')
        return f(*args, **kwargs)
    return wrapper


@app.route('/staff/login', methods=['GET', 'POST'])
def staff_login():
    """Страница входа в Штаб."""
    error = None
    if request.method == 'POST':
        password = (request.form.get('password') or '').strip()
        expected = os.getenv('STAFF_PASSWORD', '')
        if password and expected and password == expected:
            session['staff_authenticated'] = True
            session.permanent = True
            return redirect('/staff')
        error = 'Неверный пароль'
    return render_template('staff_login.html', error=error)


@app.route('/staff/logout')
def staff_logout():
    """Выход из Штаба."""
    session.pop('staff_authenticated', None)
    return redirect('/staff/login')


# === STAFF API ===

CODE_KEYWORDS = [
    'код', 'баг', 'api', 'сервер', 'база', 'оплата', 'техника', 'деплой',
    'фронт', 'бэк', 'бек', 'endpoint', 'роут', 'sql', 'python', 'flask',
    'js', 'javascript', 'nginx', 'gunicorn', 'redis', 'ssl', 'домен',
    'безопасность', 'уязвимость', 'тест', 'тестирование', 'пароль', 'токен',
]


def staff_detect_code_topic(text):
    if not text:
        return False, ''
    low = text.lower()
    found = [kw for kw in CODE_KEYWORDS if kw in low]
    return (len(found) > 0), ', '.join(found)


# Алиасы: русский + латиница → agent_id
# Алиасы для dev-окна (только Инженер и Скептик)
STAFF_DEV_ALIASES = {
    'инженер': 'engineer', 'engineer': 'engineer',
    'тестировщик': 'skeptic', 'скептик': 'skeptic', 'skeptic': 'skeptic',
}


STAFF_AGENT_ALIASES = {
    'пикч': 'pikch', 'пикчаров': 'pikch', 'pikch': 'pikch', 'pikcharov': 'pikch',
    'гармония': 'harmony', 'harmony': 'harmony',
    'архитектор': 'architect', 'архитектор инноваций': 'architect', 'architect': 'architect',
    'орбита': 'orbit', 'астра': 'orbit', 'orbit': 'orbit',
    'призрак': 'ghost', 'ghost': 'ghost',
    'инженер': 'engineer', 'engineer': 'engineer',
    'тестировщик': 'skeptic', 'скептик': 'skeptic', 'skeptic': 'skeptic',
    'система': 'system', 'system': 'system',
}


def staff_parse_agent_mention(message):
    """Парсит *agent из начала сообщения.
    Принимает русские и латинские варианты.
    Возвращает (agent_id, clean_text) или (None, original)."""
    if not message or not message.startswith('*'):
        return None, message

    comma_idx = message.find(',')
    if comma_idx == -1:
        return None, message

    raw_slug = message[1:comma_idx].strip().lower()
    if not raw_slug:
        return None, message

    agent_id = STAFF_AGENT_ALIASES.get(raw_slug)
    if not agent_id:
        return None, message

    clean = message[comma_idx + 1:].strip()
    return agent_id, clean


def staff_parse_dev_mention(message):
    """Парсит *agent в dev-окне. Только engineer или skeptic."""
    if not message or not message.startswith('*'):
        return None, message

    comma_idx = message.find(',')
    if comma_idx == -1:
        return None, message

    raw_slug = message[1:comma_idx].strip().lower()
    if not raw_slug:
        return None, message

    agent_id = STAFF_DEV_ALIASES.get(raw_slug)
    if not agent_id:
        return None, message

    clean = message[comma_idx + 1:].strip()
    return agent_id, clean


def staff_get_agents():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'agents.json')
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    s = data.get('staff', {})
    members = {m['id']: m for m in s.get('members', [])}
    sequence = s.get('sequence', [])
    return members, sequence


def staff_build_system_prompt(agent):
    aiid = agent.get('aiid', {})
    return aiid.get('prompt', aiid.get('character', ''))


def staff_call_agent(agent, messages, user_message):
    system_prompt = staff_build_system_prompt(agent)
    provider = agent.get('api_provider', 'deepseek')

    full_messages = list(messages) + [{'role': 'user', 'content': user_message}]

    if provider == 'gigachat':
        return call_gigachat(system_prompt, full_messages, temperature=0.7, max_tokens=400)
    else:
        api_key = os.getenv('DEEPSEEK_API_KEY')
        if not api_key:
            raise RuntimeError('DEEPSEEK_API_KEY not configured')
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}'
        }
        payload = {
            'model': 'deepseek-chat',
            'messages': [{'role': 'system', 'content': system_prompt}] + full_messages,
            'temperature': 0.7,
            'max_tokens': 400,
            'stream': False
        }
        r = requests.post(
            'https://api.deepseek.com/v1/chat/completions',
            json=payload, headers=headers, timeout=60
        )
        if r.status_code != 200:
            raise RuntimeError(f'DeepSeek error: {r.status_code} {r.text[:200]}')
        return r.json()['choices'][0]['message']['content']


def staff_get_messages(session_id):
    conn = sqlite3.connect('sign.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    q = "SELECT author, agent_id, content, order_num, window_type FROM staff_messages WHERE session_id = ? ORDER BY order_num ASC"
    c.execute(q, (session_id,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def staff_save_message(session_id, author, agent_id, content, order_num, window_type='staff'):
    conn = sqlite3.connect('sign.db')
    c = conn.cursor()
    q = "INSERT INTO staff_messages (session_id, author, agent_id, content, order_num, window_type) VALUES (?, ?, ?, ?, ?, ?)"
    c.execute(q, (session_id, author, agent_id, content, order_num, window_type))
    conn.commit()
    conn.close()


@app.route('/api/staff/session', methods=['POST'])
@staff_required
def staff_session_create():
    data = request.json or {}
    topic = (data.get('topic') or '').strip()
    if not topic:
        return jsonify({'error': 'topic required'}), 400
    if len(topic) > 2000:
        return jsonify({'error': 'topic too long'}), 400

    is_code, keywords = staff_detect_code_topic(topic)

    conn = sqlite3.connect('sign.db')
    c = conn.cursor()
    q = "INSERT INTO staff_sessions (topic, window_type, is_code_topic, topic_keywords, current_turn) VALUES (?, 'staff', ?, ?, 0)"
    c.execute(q, (topic, 1 if is_code else 0, keywords))
    conn.commit()
    session_id = c.lastrowid
    conn.close()

    staff_save_message(session_id, 'Основатель', None, topic, 1, 'staff')

    return jsonify({
        'status': 'ok',
        'session_id': session_id,
        'is_code_topic': is_code,
        'keywords': keywords,
        'current_turn': 0
    })


@app.route('/api/staff/turn/<int:session_id>', methods=['POST'])
@staff_required
def staff_turn(session_id):
    conn = sqlite3.connect('sign.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT id, topic, current_turn, is_code_topic, status FROM staff_sessions WHERE id = ?", (session_id,))
    session = c.fetchone()
    conn.close()

    if not session:
        return jsonify({'error': 'session not found'}), 404
    if session['status'] == 'closed':
        return jsonify({'status': 'closed', 'message': 'Сессия закрыта'}), 400

    members, sequence = staff_get_agents()
    if not sequence:
        return jsonify({'error': 'no sequence'}), 500

    current_turn = session['current_turn']
    is_code = session['is_code_topic'] == 1

    while current_turn < len(sequence):
        agent_id = sequence[current_turn]
        agent = members.get(agent_id)
        if not agent:
            current_turn += 1
            continue

        if agent_id in ('engineer', 'skeptic') and not is_code:
            staff_save_message(session_id, agent['name'], agent_id, '(пропущен — тема не про код)', current_turn + 2, 'staff')
            current_turn += 1
            continue

        try:
            history = staff_get_messages(session_id)
            api_messages = []
            for h in history:
                role = 'user' if h['author'] == 'Основатель' else 'assistant'
                api_messages.append({'role': role, 'content': h['content']})
            last_user = ''
            for h in reversed(history):
                if h['author'] == 'Основатель':
                    last_user = h['content']
                    break

            turn_context = (
                f"\n\n[КОНТЕКСТ ШТАБА]\n"
                f"Тема Штаба: {session['topic']}\n"
                f"Твой ход: {current_turn + 1} из {len(sequence)}\n"
                f"Ты отвечаешь как {agent['name']}.\n"
                f"Отвечай коротко, 1-2 предложения, по своей роли.\n"
                f"Не повторяй других. Не задавай вопросы — давай свой взгляд."
            )

            # Retry: 2 попытки
            response = None
            last_error = ''
            for attempt in range(2):
                try:
                    response = staff_call_agent(agent, api_messages[:-1], last_user + turn_context)
                    break
                except Exception as e:
                    last_error = str(e)[:150]
                    print(f'[STAFF] {agent["name"]} attempt {attempt+1} failed: {last_error}')
                    if attempt == 0:
                        import time as _t
                        _t.sleep(2)
                    continue

            if response is None:
                # Все попытки провалились — сохраняем как ошибку, пропускаем агента
                staff_save_message(
                    session_id,
                    agent['name'],
                    agent_id,
                    f'(ошибка API: {last_error})',
                    current_turn + 2,
                    'staff'
                )
                current_turn += 1
                conn = sqlite3.connect('sign.db')
                c = conn.cursor()
                c.execute("UPDATE staff_sessions SET current_turn = ? WHERE id = ?",
                          (current_turn, session_id))
                conn.commit()
                conn.close()
                continue
        except Exception as e:
            return jsonify({'error': f'API error: {str(e)[:200]}'}), 500

        staff_save_message(session_id, agent['name'], agent_id, response, current_turn + 2, 'staff')
        current_turn += 1

        conn = sqlite3.connect('sign.db')
        c = conn.cursor()
        c.execute("UPDATE staff_sessions SET current_turn = ? WHERE id = ?", (current_turn, session_id))
        conn.commit()
        conn.close()

        return jsonify({
            'status': 'ok',
            'agent_id': agent_id,
            'agent_name': agent['name'],
            'api_provider': agent.get('api_provider', ''),
            'avatar': agent.get('avatar'),
            'content': response,
            'current_turn': current_turn,
            'total_turns': len(sequence),
            'is_final': current_turn >= len(sequence)
        })

    conn = sqlite3.connect('sign.db')
    c = conn.cursor()
    c.execute("UPDATE staff_sessions SET status = 'awaiting_decision' WHERE id = ?", (session_id,))
    conn.commit()
    conn.close()

    return jsonify({
        'status': 'awaiting_decision',
        'current_turn': current_turn,
        'total_turns': len(sequence)
    })


@app.route('/api/staff/close/<int:session_id>', methods=['POST'])
@staff_required
def staff_close(session_id):
    conn = sqlite3.connect('sign.db')
    c = conn.cursor()
    c.execute("UPDATE staff_sessions SET status = 'closed', ended_at = CURRENT_TIMESTAMP WHERE id = ?", (session_id,))
    conn.commit()
    conn.close()
    return jsonify({'status': 'ok', 'message': 'Сессия закрыта'})


@app.route('/api/staff/continue/<int:session_id>', methods=['POST'])
@staff_required
def staff_continue(session_id):
    data = request.json or {}
    comment = (data.get('comment') or '').strip()
    if not comment:
        return jsonify({'error': 'comment required'}), 400

    conn = sqlite3.connect('sign.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT COUNT(*) as cnt FROM staff_messages WHERE session_id = ?", (session_id,))
    order = c.fetchone()['cnt'] + 1
    c.execute("UPDATE staff_sessions SET current_turn = 0, status = 'active' WHERE id = ?", (session_id,))
    conn.commit()
    conn.close()

    staff_save_message(session_id, 'Основатель', None, comment, order, 'staff')
    return jsonify({'status': 'ok', 'message': 'Продолжаем', 'order_num': order})


@app.route('/api/staff/personal', methods=['POST'])
@staff_required
def staff_personal():
    """Личный диалог: *agent, сообщение."""
    data = request.json or {}
    raw = (data.get('message') or '').strip()

    if not raw:
        return jsonify({'error': 'message required'}), 400

    agent_id, clean_text = staff_parse_agent_mention(raw)
    if not agent_id:
        return jsonify({
            'error': 'Вызовите нужного агента. Формат: *агент, вопрос.',
            'code': 'no_agent'
        }), 400

    if not clean_text:
        return jsonify({
            'error': 'Добавьте вопрос после имени агента.',
            'code': 'no_question'
        }), 400

    members, _ = staff_get_agents()
    agent = members.get(agent_id)
    if not agent:
        return jsonify({'error': 'agent not found'}), 404

    conn = sqlite3.connect('sign.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT id FROM staff_sessions WHERE window_type = 'personal' AND status = 'active' ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    if row:
        session_id = row['id']
    else:
        c.execute("INSERT INTO staff_sessions (topic, window_type, status) VALUES (?, 'personal', 'active')", ('Личный диалог',))
        session_id = c.lastrowid
    conn.commit()
    conn.close()

    conn = sqlite3.connect('sign.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT author, agent_id, content, order_num FROM staff_messages WHERE session_id = ? AND window_type = 'personal' ORDER BY order_num ASC", (session_id,))
    history = [dict(r) for r in c.fetchall()]
    c.execute("SELECT COUNT(*) as cnt FROM staff_messages WHERE session_id = ?", (session_id,))
    order_base = c.fetchone()['cnt']
    conn.close()

    order_user = order_base + 1
    staff_save_message(session_id, 'Основатель', None, raw, order_user, 'personal')

    api_messages = []
    for h in history:
        role = 'user' if h['author'] == 'Основатель' else 'assistant'
        api_messages.append({'role': role, 'content': h['content']})

    try:
        response = staff_call_agent(agent, api_messages, clean_text)
    except Exception as e:
        return jsonify({'error': f'API error: {str(e)[:200]}'}), 500

    order_ai = order_user + 1
    staff_save_message(session_id, agent['name'], agent_id, response, order_ai, 'personal')

    # Инкрементим current_turn (для отображения в истории)
    conn = sqlite3.connect('sign.db')
    c = conn.cursor()
    c.execute("UPDATE staff_sessions SET current_turn = current_turn + 1 WHERE id = ?", (session_id,))
    conn.commit()
    conn.close()

    return jsonify({
        'status': 'ok',
        'session_id': session_id,
        'agent_id': agent_id,
        'agent_name': agent['name'],
        'api_provider': agent.get('api_provider', ''),
        'avatar': agent.get('avatar'),
        'content': response,
        'order_num': order_ai
    })


@app.route('/api/staff/personal/history', methods=['GET'])
@staff_required
def staff_personal_history():
    conn = sqlite3.connect('sign.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT id FROM staff_sessions WHERE window_type = 'personal' ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    if not row:
        conn.close()
        return jsonify({'status': 'ok', 'messages': []})

    session_id = row['id']
    c.execute("SELECT author, agent_id, content, order_num FROM staff_messages WHERE session_id = ? AND window_type = 'personal' ORDER BY order_num ASC", (session_id,))
    messages = [dict(r) for r in c.fetchall()]
    conn.close()

    return jsonify({'status': 'ok', 'session_id': session_id, 'messages': messages})


@app.route('/api/staff/dev', methods=['POST'])
@staff_required
def staff_dev():
    """Техразработка: *инженер / *тестировщик, сообщение."""
    data = request.json or {}
    raw = (data.get('message') or '').strip()

    if not raw:
        return jsonify({'error': 'message required'}), 400

    agent_id, clean_text = staff_parse_dev_mention(raw)
    if not agent_id:
        return jsonify({
            'error': 'Вызовите нужного агента. Формат: *инженер, вопрос или *тестировщик, вопрос.',
            'code': 'no_agent'
        }), 400

    if not clean_text:
        return jsonify({'error': 'Добавьте вопрос.', 'code': 'no_question'}), 400

    members, _ = staff_get_agents()
    agent = members.get(agent_id)
    if not agent:
        return jsonify({'error': 'agent not found'}), 404

    conn = sqlite3.connect('sign.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT id FROM staff_sessions WHERE window_type = 'dev' AND status = 'active' ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    if row:
        session_id = row['id']
    else:
        c.execute("INSERT INTO staff_sessions (topic, window_type, status) VALUES (?, 'dev', 'active')", ('Техразработка',))
        session_id = c.lastrowid
    conn.commit()
    conn.close()

    conn = sqlite3.connect('sign.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT author, agent_id, content, order_num FROM staff_messages WHERE session_id = ? AND window_type = 'dev' ORDER BY order_num ASC", (session_id,))
    history = [dict(r) for r in c.fetchall()]
    c.execute("SELECT COUNT(*) as cnt FROM staff_messages WHERE session_id = ?", (session_id,))
    order_base = c.fetchone()['cnt']
    conn.close()

    order_user = order_base + 1
    staff_save_message(session_id, 'Основатель', None, raw, order_user, 'dev')

    api_messages = []
    for h in history:
        role = 'user' if h['author'] == 'Основатель' else 'assistant'
        api_messages.append({'role': role, 'content': h['content']})

    try:
        response = staff_call_agent(agent, api_messages, clean_text)
    except Exception as e:
        return jsonify({'error': f'API error: {str(e)[:200]}'}), 500

    order_ai = order_user + 1
    staff_save_message(session_id, agent['name'], agent_id, response, order_ai, 'dev')

    conn = sqlite3.connect('sign.db')
    c = conn.cursor()
    c.execute("UPDATE staff_sessions SET current_turn = current_turn + 1 WHERE id = ?", (session_id,))
    conn.commit()
    conn.close()

    return jsonify({
        'status': 'ok',
        'session_id': session_id,
        'agent_id': agent_id,
        'agent_name': agent['name'],
        'api_provider': agent.get('api_provider', ''),
        'avatar': agent.get('avatar'),
        'content': response,
        'order_num': order_ai
    })


@app.route('/api/staff/dev/history', methods=['GET'])
@staff_required
def staff_dev_history():
    conn = sqlite3.connect('sign.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT id FROM staff_sessions WHERE window_type = 'dev' ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    if not row:
        conn.close()
        return jsonify({'status': 'ok', 'messages': []})

    session_id = row['id']
    c.execute("SELECT author, agent_id, content, order_num FROM staff_messages WHERE session_id = ? AND window_type = 'dev' ORDER BY order_num ASC", (session_id,))
    messages = [dict(r) for r in c.fetchall()]
    conn.close()

    return jsonify({'status': 'ok', 'session_id': session_id, 'messages': messages})


@app.route('/api/staff/sessions', methods=['GET'])
@staff_required
def staff_sessions_list():
    conn = sqlite3.connect('sign.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT id, topic, status, is_code_topic, current_turn, started_at, ended_at FROM staff_sessions ORDER BY started_at DESC LIMIT 50")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return jsonify({'status': 'ok', 'sessions': rows})


@app.route('/api/staff/session/<int:session_id>', methods=['GET'])
@staff_required
def staff_session_view(session_id):
    conn = sqlite3.connect('sign.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM staff_sessions WHERE id = ?", (session_id,))
    session = c.fetchone()
    conn.close()

    if not session:
        return jsonify({'error': 'not found'}), 404

    messages = staff_get_messages(session_id)
    return jsonify({
        'status': 'ok',
        'session': dict(session),
        'messages': messages
    })

@app.route('/staff')
@staff_required
def staff():
    """Штаб Eidos — три окна диалога."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'agents.json')
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    staff_data = data.get('staff', {})
    members = {m['id']: m for m in staff_data.get('members', [])}
    sequence = staff_data.get('sequence', [])

    # Формируем список для frontend
    staff_agents = []
    for aid in sequence:
        if aid in members:
            m = members[aid]
            staff_agents.append({
                'id': m['id'],
                'code': m.get('code', ''),
                'name': m['name'],
                'role': m.get('role', ''),
                'api_provider': m.get('api_provider', ''),
                'turn': m.get('turn', 0),
                'avatar': m.get('avatar'),
                'main_phrase': m.get('aiid', {}).get('main_phrase', ''),
            })

    return render_template('staff.html', staff_agents=staff_agents, sequence=sequence)


@app.route('/city')
def city():
    """Страница Совета Города: триада + дополняющие."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'agents.json')
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    city_data = data.get('city', {})
    return render_template(
        'city.html',
        triad=city_data.get('triad', []),
        extra=city_data.get('extra', [])
    )

@app.route('/chat')
def chat():
    """Страница чата: 32 агента (4 группы × 8)."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'agents.json')
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # Группы: порядок и метки
    GROUPS = [
        {'id': 'lovers',  'label': 'Любители', 'color': '#00c896'},
        {'id': 'profile', 'label': 'Профи',     'color': '#b8c4d0'},
        {'id': 'experts', 'label': 'Эксперты',  'color': '#a020f0'},
        {'id': 'legends', 'label': 'Легенды',   'color': '#ffd700'},
    ]

    # Собираем всех chat-агентов
    all_agents = []
    for a in data.get('agents', []):
        cg = a.get('chat_group')
        if not cg:
            continue
        # Находим метку и цвет группы
        group_meta = next((g for g in GROUPS if g['id'] == cg), None)
        if not group_meta:
            continue
        all_agents.append({
            'id': a['id'],
            'code': a.get('code', ''),
            'name': a['name'],
            'role': a.get('role', ''),
            'tagline': a.get('tagline', ''),
            'short': a.get('short', ''),
            'genre': a.get('genre', ''),
            'chat_group': cg,
            'chat_label': group_meta['label'],
            'group_color': a.get('group_color', group_meta['color']),
            'limit': a.get('limit', 5),
            'price': a.get('price', 0),
            'avatar': a.get('avatar', ''),
            'main_phrase': a.get('main_phrase', ''),
            'aiid': {
                'id': a.get('code', a['id']),
                'version': '1.0',
                'date': a.get('date', '2026'),
                'genre': a.get('genre', ''),
                'owner': 'Eidos Galaxy xPandify',
                'type': group_meta['label'],
                'dna': a.get('dna', {}),
                'character': a.get('soul', ''),
                'functions': a.get('functions', []),
                'communication_rules': a.get('contract', ''),
                'limits': a.get('limits', []),
                'strengths': a.get('strengths', []),
                'main_phrase': a.get('main_phrase', ''),
            }
        })

    # Группируем и сортируем внутри групп по id
    groups_with_agents = []
    for g in GROUPS:
        g_agents = sorted(
            [a for a in all_agents if a['chat_group'] == g['id']],
            key=lambda x: x['id']
        )
        groups_with_agents.append({
            'id': g['id'],
            'label': g['label'],
            'color': g['color'],
            'agents': g_agents,
        })

    return render_template(
        'chat.html',
        groups=groups_with_agents,
        chat_agents=all_agents,  # для JSON-данных
    )


# === GIGACHAT API ===
import time as _time

_gigachat_token_cache = {'token': None, 'expires_at': 0}

def get_gigachat_token():
    """Возвращает валидный OAuth-токен GigaChat. Обновляет раз в 30 мин."""
    now = _time.time()
    if _gigachat_token_cache['token'] and _gigachat_token_cache['expires_at'] > now + 60:
        return _gigachat_token_cache['token']

    client_secret = os.getenv('GIGACHAT_CLIENT_SECRET', '')
    if not client_secret:
        raise ValueError('GIGACHAT_CLIENT_SECRET not configured')

    import uuid as _uuid
    auth_url = 'https://ngw.devices.sberbank.ru:9443/api/v2/oauth'
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Accept': 'application/json',
        'RqUID': str(_uuid.uuid4()),
        'Authorization': f'Bearer {client_secret}'
    }
    data = {'scope': 'GIGACHAT_API_PERS'}

    r = requests.post(auth_url, headers=headers, data=data, timeout=45)
    if r.status_code != 200:
        raise RuntimeError(f'GigaChat OAuth failed: {r.status_code} {r.text[:200]}')

    result = r.json()
    token = result['access_token']
    # GigaChat возвращает expires_at в миллисекундах
    expires_at = result.get('expires_at', 0) / 1000  # → секунды
    if not expires_at:
        expires_at = now + 25 * 60  # fallback: 25 мин

    _gigachat_token_cache['token'] = token
    _gigachat_token_cache['expires_at'] = expires_at
    return token


def call_gigachat(system_prompt, messages, temperature=0.7, max_tokens=1000):
    """Вызов GigaChat API. Возвращает строку ответа (как DeepSeek).

    Args:
        system_prompt: str — system message
        messages: list — [{role, content}, ...] — история
        temperature: float
        max_tokens: int

    Returns:
        str — ответ ассистента
    """
    token = get_gigachat_token()

    chat_url = 'https://gigachat.devices.sberbank.ru/api/v1/chat/completions'

    # Формируем полный список сообщений
    full_messages = [{'role': 'system', 'content': system_prompt}]
    for m in messages:
        full_messages.append({
            'role': m.get('role', 'user'),
            'content': m.get('content', '')
        })

    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Authorization': f'Bearer {token}'
    }
    payload = {
        'model': 'GigaChat',
        'messages': full_messages,
        'temperature': temperature,
        'max_tokens': max_tokens
    }

    r = requests.post(chat_url, headers=headers, json=payload, timeout=90)
    if r.status_code != 200:
        raise RuntimeError(f'GigaChat chat failed: {r.status_code} {r.text[:300]}')

    result = r.json()
    return result['choices'][0]['message']['content']


def build_agent_system_prompt(agent_id, user_name=''):
    """Собирает system prompt для выбранного агента из agents.json."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'agents.json')
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    for a in data.get('agents', []):
        if a['id'] == agent_id and a.get('chat_group'):
            name_line = ''
            if user_name:
                name_line = (
                    f"\n\nВАЖНО: Пользователя зовут {user_name}. "
                    f"Обращайся к нему по имени, правильно определяй род "
                    f"(мужской/женский) по имени. Не пиши в стиле '{user_name}(а)' — "
                    f"это неверно. Если род определить невозможно — используй нейтральные формулировки."
                )
            return (
                f"Ты — {a['name']}, {a['role']}.\n\n"
                f"ХАРАКТЕР:\n{a.get('soul', '')}\n\n"
                f"ФУНКЦИИ:\n" + "\n".join(f"- {f}" for f in a.get('functions', [])) + "\n\n"
                f"ПРАВИЛА ОБЩЕНИЯ:\n{a.get('contract', '')}\n\n"
                f"ОГРАНИЧЕНИЯ:\n" + "\n".join(f"- {x}" for x in a.get('limits', [])) + "\n\n"
                f"СИЛЬНЫЕ СТОРОНЫ:\n" + "\n".join(f"- {x}" for x in a.get('strengths', []))
                + name_line
            )
    return "Ты — цифровой помощник."


def build_luch_system_prompt():
    """Собирает system prompt для LUCH из agents.json."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'agents.json')
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    for a in data.get('agents', []):
        if a['id'] == 'luch':
            return (
                f"Ты — {a['name']}, {a['role']}.\n\n"
                f"ХАРАКТЕР:\n{a.get('soul', '')}\n\n"
                f"ФУНКЦИИ:\n" + "\n".join(f"- {f}" for f in a.get('functions', [])) + "\n\n"
                f"ПРАВИЛА ОБЩЕНИЯ:\n{a.get('contract', '')}\n\n"
                f"ОГРАНИЧЕНИЯ:\n" + "\n".join(f"- {x}" for x in a.get('limits', [])) + "\n\n"
                f"СИЛЬНЫЕ СТОРОНЫ:\n" + "\n".join(f"- {x}" for x in a.get('strengths', []))
            )
    return "Ты — LUCH, агент света и надежды."


@app.route('/api/chat/message', methods=['POST'])
@limiter.limit("200 per hour")
def chat_message():
    """Одно сообщение пользователя → ответ выбранного агента через DeepSeek."""
    # HMAC-проверка
    device_id = request.headers.get('X-Device-ID', '')
    sig = request.headers.get('X-Device-Sig', '')
    ok, err = check_device_auth(device_id, sig)
    if not ok:
        return err

    data = request.json or {}
    user_message = (data.get('message') or '').strip()
    agent_id = (data.get('agent_id') or '').strip()
    user_name = (data.get('user_name') or '').strip()[:50]

    if not user_message:
        return jsonify({'error': 'message required'}), 400
    if len(user_message) > 2000:
        return jsonify({'error': 'message too long (max 2000)'}), 400
    if not agent_id:
        return jsonify({'error': 'agent_id required'}), 400

    # Динамическая проверка: agent_id — chat-агент в agents.json
    agents_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'agents.json')
    with open(agents_path, 'r', encoding='utf-8') as f:
        agents_data = json.load(f)
    allowed = {a['id'] for a in agents_data.get('agents', []) if a.get('chat_group')}
    if agent_id not in allowed:
        return jsonify({'error': 'unknown agent'}), 404

    # API-ключ
    api_key = os.getenv('DEEPSEEK_API_KEY')
    if not api_key:
        return jsonify({'error': 'API key not configured'}), 500

    # System prompt выбранного агента + имя пользователя
    system_prompt = build_agent_system_prompt(agent_id, user_name)

    # Вызов DeepSeek
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {api_key}'
    }
    payload = {
        'model': 'deepseek-chat',
        'messages': [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_message}
        ],
        'temperature': 0.7,
        'max_tokens': 1000,
        'stream': False
    }

    try:
        resp = requests.post(
            'https://api.deepseek.com/v1/chat/completions',
            json=payload,
            headers=headers,
            timeout=30
        )
        if resp.status_code != 200:
            log_reject('deepseek_error', device_id, {'status': resp.status_code, 'body': resp.text[:200]})
            return jsonify({'error': 'DeepSeek API error', 'status': resp.status_code}), 502

        result = resp.json()
        assistant_message = result['choices'][0]['message']['content']
        tokens = result.get('usage', {}).get('total_tokens', 0)

        # Имя агента
        agent_name = agent_id
        for a in agents_data.get('agents', []):
            if a['id'] == agent_id:
                agent_name = a.get('name', agent_id)
                break

        return jsonify({
            'status': 'ok',
            'participant': agent_name,
            'agent_id': agent_id,
            'content': assistant_message,
            'tokens': tokens
        })

    except requests.Timeout:
        log_reject('deepseek_timeout', device_id)
        return jsonify({'error': 'DeepSeek timeout'}), 504
    except Exception as e:
        log_reject('deepseek_exception', device_id, {'error': str(e)})
        return jsonify({'error': 'internal error'}), 500


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
