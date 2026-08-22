from flask import request, jsonify, render_template
from app import app
from app.auth import get_or_create_user, can_create_free, consume_free_slot
from app.dna import calculate_dna
from app.aiid import generate_aiid_code, build_aiid
from app.models import log_aiid
import json

@app.route('/api/generate_free', methods=['POST'])
def generate_free():
    data = request.json
    device_id = request.headers.get('X-Device-ID', 'unknown')
    genre = data.get('genre')
    specialization = data.get('specialization', '')
    owner_name = data.get('owner_name', 'Агент')
    points = data.get('points', [])

    user = get_or_create_user(device_id)
    can, reason = can_create_free(user)
    if not can:
        return jsonify({'status': 'error', 'message': 'Лимит исчерпан', 'reason': reason}), 403

    dna = calculate_dna(points)
    aiid_code = generate_aiid_code(genre)
    content = build_aiid(genre, specialization, owner_name, dna, 'DEMO', aiid_code)

    consume_free_slot(user)
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