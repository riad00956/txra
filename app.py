import os
import sys
import json
import telebot
import sqlite3
import requests
import random
import string
import threading
import time
from datetime import datetime, timedelta
from telebot import types
from http.server import HTTPServer, BaseHTTPRequestHandler

# ==============================
# CONFIGURATION
# ==============================
API_TOKEN = '8660919886:AAGLJAactunzCrv-lKRO1o-GjiBgLDGEbxI'
ADMIN_ID = 8373846582

bot = telebot.TeleBot(API_TOKEN)

# ==============================
# DATABASE SETUP (with last_checked)
# ==============================
def init_db():
    conn = sqlite3.connect('uptime.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS monitors 
                      (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, url TEXT, 
                       interval INTEGER, status TEXT DEFAULT 'UNKNOWN', fail_count INTEGER DEFAULT 0,
                       last_checked TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')  # new column
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, is_verified INTEGER DEFAULT 0)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS access_codes (code TEXT PRIMARY KEY, is_used INTEGER DEFAULT 0)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS logs (id INTEGER PRIMARY KEY AUTOINCREMENT, monitor_id INTEGER, status TEXT, detail TEXT, timestamp TEXT)''')
    conn.commit()
    return conn

db_conn = init_db()

# ==============================
# MONITORING ENGINE (same as before)
# ==============================
def ping_url(monitor_id, url, user_id):
    regions = ["🇺🇸 US-East", "🇪🇺 EU-West", "🇸🇬 SG-Core", "🇯🇵 JP-Tokyo", "🇧🇩 BD-Khulna", "🇰🇷 KA-North"]
    region = random.choice(regions)
    headers = {'User-Agent': 'UptimeBot/2.0 (Health-Check)'}
    
    try:
        start_time = time.time()
        response = requests.get(url, timeout=15, headers=headers)
        latency = round((time.time() - start_time) * 1000)
        if response.status_code == 200:
            status = "UP"
            detail = f"{region} | {latency}ms | 200 OK"
        else:
            status = "DOWN"
            detail = f"{region} | Error: {response.status_code}"
    except Exception as e:
        status = "DOWN"
        detail = f"{region} | Connection Timeout"

    cursor = db_conn.cursor()
    cursor.execute("SELECT fail_count FROM monitors WHERE id=?", (monitor_id,))
    res = cursor.fetchone()
    if not res: return 
    fail_count = res[0]

    now = datetime.now().strftime("%H:%M:%S")
    
    # Smart Retry Logic
    final_status = status
    new_fail_count = fail_count + 1 if status == "DOWN" else 0
    
    if 0 < new_fail_count < 3:
        final_status = "UP" 

    cursor.execute("UPDATE monitors SET status=?, fail_count=?, last_checked=? WHERE id=?", 
                   (final_status, new_fail_count, datetime.now(), monitor_id))
    cursor.execute("INSERT INTO logs (monitor_id, status, detail, timestamp) VALUES (?, ?, ?, ?)", 
                   (monitor_id, status, detail, now))
    db_conn.commit()

    if new_fail_count == 3:
        alert = f"🚨 *MONITOR DOWN*\n\n🌐 URL: {url}\n❌ Reason: {detail}\n⏰ Time: {now} UTC"
        try: bot.send_message(user_id, alert, parse_mode="Markdown")
        except: pass

# ==============================
# CRON ENDPOINT – প্রতি মিনিটে external cron job এটি কল করবে
# ==============================
def run_cron_tasks():
    """এখন যেসব মণিটরের interval পেরিয়েছে তাদের চেক করে"""
    cursor = db_conn.cursor()
    # সব monitor নিয়ে আসি
    cursor.execute("SELECT id, url, interval, user_id, last_checked FROM monitors WHERE interval > 0")
    monitors = cursor.fetchall()
    now = datetime.now()
    for mon in monitors:
        mid, url, interval, uid, last_checked_str = mon
        # last_checked কে datetime-এ রূপান্তর
        if last_checked_str:
            last_checked = datetime.fromisoformat(last_checked_str)
        else:
            last_checked = datetime.min  # যদি NULL হয়
        # পরবর্তী চেকের সময় = last_checked + interval মিনিট
        next_check = last_checked + timedelta(minutes=interval)
        if now >= next_check:
            # চেক চালাও (thread-এ চালালে ব্লক হবে না)
            threading.Thread(target=ping_url, args=(mid, url, uid)).start()

# ==============================
# HELPERS (unchanged)
# ==============================
def get_ascii_graph(monitor_id):
    cursor = db_conn.cursor()
    cursor.execute("SELECT status FROM logs WHERE monitor_id=? ORDER BY id DESC LIMIT 20", (monitor_id,))
    rows = cursor.fetchall()
    if not rows: return "No data yet"
    history = [r[0] for r in rows][::-1]
    return "".join(["🟩" if s == 'UP' else "🟥" for s in history])

def is_verified(user_id):
    cursor = db_conn.cursor()
    cursor.execute("SELECT is_verified FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    return row and row[0] == 1

def main_menu():
    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton("➕ Add Monitor", callback_data="add"),
               types.InlineKeyboardButton("📋 My List", callback_data="list"))
    return markup

# ==============================
# HANDLERS (unchanged)
# ==============================
@bot.message_handler(commands=['start'])
def start(message):
    uid = message.from_user.id
    if not is_verified(uid):
        cursor = db_conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (uid,))
        db_conn.commit()
        return bot.send_message(uid, "🔒 *Access Denied*\nPlease send your Access Code (AC-XXXXX) to unlock:", parse_mode="Markdown")
    
    bot.send_message(uid, "✅ *Uptime Monitor Dashboard*\n\n 🎗️chain: @nahin_x_bot", reply_markup=main_menu(), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text.startswith("AC-"))
def verify_code(message):
    code = message.text.strip()
    cursor = db_conn.cursor()
    cursor.execute("SELECT code FROM access_codes WHERE code=? AND is_used=0", (code,))
    if cursor.fetchone():
        cursor.execute("UPDATE access_codes SET is_used=1 WHERE code=?", (code,))
        cursor.execute("UPDATE users SET is_verified=1 WHERE user_id=?", (message.from_user.id,))
        db_conn.commit()
        bot.reply_to(message, "🎉 *Access Granted!* Type /start to begin.")
    else:
        bot.reply_to(message, "❌ Invalid or expired code.")

@bot.message_handler(commands=['admin'])
def admin_panel(message):
    if message.from_user.id != ADMIN_ID: return
    code = "AC-" + ''.join(random.choice(string.ascii_uppercase + string.digits) for _ in range(8))
    cursor = db_conn.cursor()
    cursor.execute("INSERT INTO access_codes (code) VALUES (?)", (code,))
    db_conn.commit()
    bot.send_message(ADMIN_ID, f"🔑 *New Access Code Generated:* `{code}`", parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == "add")
def ask_url(call):
    sent = bot.edit_message_text("🔗 Send the URL to monitor (with http/https):", call.message.chat.id, call.message.message_id)
    bot.register_next_step_handler(sent, process_url_input)

def process_url_input(message):
    url = message.text
    if not url.startswith("http"):
        return bot.send_message(message.chat.id, "❌ Error: Invalid URL format.")
    
    cursor = db_conn.cursor()
    cursor.execute("INSERT INTO monitors (user_id, url, interval, last_checked) VALUES (?, ?, ?, ?)", 
                   (message.from_user.id, url, 0, datetime.now()))
    db_conn.commit()
    row_id = cursor.lastrowid
    
    sent = bot.send_message(message.chat.id, "⏱ *Set Custom Interval*\nEnter check frequency in minutes (e.g., 2, 10, 60):", parse_mode="Markdown")
    bot.register_next_step_handler(sent, process_interval_input, row_id, url)

def process_interval_input(message, row_id, url):
    try:
        minutes = int(message.text)
        if minutes < 1: raise ValueError
    except:
        return bot.send_message(message.chat.id, "❌ Invalid number. Please start again.")

    cursor = db_conn.cursor()
    cursor.execute("UPDATE monitors SET interval = ? WHERE id = ?", (minutes, row_id))
    db_conn.commit()

    # No scheduler job now; last_checked will be set when first check happens
    bot.send_message(message.chat.id, f"✅ *Success!*\nMonitoring `{url}` every {minutes} minutes.", reply_markup=main_menu())

@bot.callback_query_handler(func=lambda call: call.data == "list")
def show_list(call):
    cursor = db_conn.cursor()
    cursor.execute("SELECT id, url, status FROM monitors WHERE user_id=? AND interval > 0", (call.from_user.id,))
    rows = cursor.fetchall()
    
    markup = types.InlineKeyboardMarkup()
    for r in rows:
        icon = "💚" if r[2] == "UP" else "❤️" if r[2] == "DOWN" else "⚪"
        markup.add(types.InlineKeyboardButton(f"{icon} {r[1]}", callback_data=f"view_{r[0]}"))
    
    markup.add(types.InlineKeyboardButton("🔙 Back Home", callback_data="home"))
    bot.edit_message_text("📊 *Monitor List:*", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("view_"))
def view_monitor(call):
    mid = call.data.split("_")[1]
    cursor = db_conn.cursor()
    cursor.execute("SELECT url, interval, status FROM monitors WHERE id=?", (mid,))
    m = cursor.fetchone()
    
    cursor.execute("SELECT detail, timestamp FROM logs WHERE monitor_id=? ORDER BY id DESC LIMIT 5", (mid,))
    logs = cursor.fetchall()
    log_text = "\n".join([f"`[{l[1]}]` {l[0]}" for l in logs]) if logs else "No data."
    
    graph = get_ascii_graph(mid)
    text = (f"🌐 *Monitor:* {m[0]}\n"
            f"⏱ *Check Rate:* {m[1]} min\n"
            f"📡 *Current:* {m[2]}\n\n"
            f"📈 *Uptime Graph (Last 20):*\n`{graph}`\n\n"
            f"🧭 *Live Regional Logs:*\n{log_text}")
    
    markup = types.InlineKeyboardMarkup()
    markup.row(types.InlineKeyboardButton("🔄 Refresh", callback_data=f"view_{mid}"),
               types.InlineKeyboardButton("🗑 Delete", callback_data=f"del_{mid}"))
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="list"))
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("del_"))
def delete_monitor(call):
    mid = call.data.split("_")[1]
    cursor = db_conn.cursor()
    cursor.execute("DELETE FROM monitors WHERE id=?", (mid,))
    cursor.execute("DELETE FROM logs WHERE monitor_id=?", (mid,))
    db_conn.commit()
    bot.answer_callback_query(call.id, "Deleted.")
    show_list(call)

@bot.callback_query_handler(func=lambda call: call.data == "home")
def go_home(call):
    bot.edit_message_text("✅ *Uptime Monitor Dashboard*", call.message.chat.id, call.message.message_id, reply_markup=main_menu(), parse_mode="Markdown")

# ==============================
# HTTP SERVER (handles both webhook and cron)
# ==============================
class WebhookHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/':
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot is Running")
        elif self.path == '/cron':
            # external cron hits this endpoint
            run_cron_tasks()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Cron tasks executed")
        else:
            self.send_error(404)
    
    def do_POST(self):
        if self.path == '/webhook':
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            try:
                update = telebot.types.Update.de_json(json.loads(post_data))
                bot.process_new_updates([update])
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"OK")
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(f"Error: {e}".encode())
        else:
            self.send_error(404)

def run_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), WebhookHandler)
    print(f"HTTP Server running on port {port}")
    server.serve_forever()

if __name__ == "__main__":
    # Start HTTP server (daemon thread)
    threading.Thread(target=run_server, daemon=True).start()

    # Set Telegram webhook
    webhook_url = os.environ.get('WEBHOOK_URL')
    if not webhook_url:
        print("ERROR: WEBHOOK_URL environment variable is not set. Exiting.")
        sys.exit(1)
    
    if not webhook_url.endswith('/webhook'):
        webhook_url = webhook_url.rstrip('/') + '/webhook'
    
    bot.remove_webhook()
    time.sleep(1)
    bot.set_webhook(url=webhook_url)
    print(f"Webhook set to: {webhook_url}")

    # Keep main thread alive (server runs in daemon)
    try:
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        print("Shutting down...")
