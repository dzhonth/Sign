import sqlite3
import json
from datetime import datetime
from app.config import Config

def get_db():
    conn = sqlite3.connect(Config.DATABASE)
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
    c.execute('''CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        payment_id TEXT UNIQUE NOT NULL,
        amount INTEGER NOT NULL,
        status TEXT DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )''')
    conn.commit()
    conn.close()

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

def confirm_payment(payment_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('UPDATE payments SET status = "paid" WHERE payment_id = ?', (payment_id,))
    c.execute('SELECT user_id FROM payments WHERE payment_id = ?', (payment_id,))
    row = c.fetchone()
    if row:
        c.execute('UPDATE users SET type = "paid", aiid_count = 0 WHERE id = ?', (row['user_id'],))
    conn.commit()
    conn.close()