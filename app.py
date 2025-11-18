import sqlite3
import logging
import os
import asyncio
import base64
from datetime import datetime, timedelta
from fastapi import FastAPI, Request
import httpx

# -----------------------------------------
# LOGGING SETUP
# -----------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()
@app.get("/")
async def root():
    return {"status": "ok", "message": "Dental bot is running"}

# -----------------------------------------
# CONFIG & ENV
# -----------------------------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
TELEGRAM_FILE_URL = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}"

# Configuration for Gemini Model
GEMINI_MODEL = "gemini-1.5-flash" # Or gemini-2.0-flash-exp depending on availability

if not TELEGRAM_TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN is missing.")
if not GOOGLE_API_KEY:
    logger.error("GOOGLE_API_KEY is missing.")

# -----------------------------------------
# DATABASE SETUP (SQLite)
# -----------------------------------------
DB_NAME = "clinic_v2.db"

def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        # Users Table
        c.execute('''
            CREATE TABLE IF NOT EXISTS users (
                chat_id INTEGER PRIMARY KEY,
                name TEXT,
                phone TEXT,
                lang TEXT DEFAULT 'fa',
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Appointments Table (Smart Booking)
        c.execute('''
            CREATE TABLE IF NOT EXISTS appointments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_chat_id INTEGER,
                service TEXT,
                doctor TEXT,
                slot_datetime TEXT,  -- ISO format: YYYY-MM-DD HH:MM
                status TEXT DEFAULT 'confirmed',
                reminded BOOLEAN DEFAULT 0
            )
        ''')
        conn.commit()

init_db()

# -----------------------------------------
# BACKGROUND TASK: APPOINTMENT REMINDER
# -----------------------------------------
@app.on_event("startup")
async def start_scheduler():
    """Starts the background loop for reminders."""
    asyncio.create_task(reminder_loop())

async def reminder_loop():
    """Checks every hour for appointments in the next 24h."""
    while True:
        try:
            now = datetime.now()
            tomorrow = now + timedelta(days=1)
            # Simple logic: Find appointments for 'tomorrow' (roughly) that haven't been reminded
            # In a real app, query mostly by strict time ranges.
            
            with sqlite3.connect(DB_NAME) as conn:
                conn.row_factory = sqlite3.Row
                c = conn.cursor()
                # Find confirmed appointments scheduled for tomorrow (date match)
                target_date_str = tomorrow.strftime("%Y-%m-%d")
                c.execute('''
                    SELECT * FROM appointments 
                    WHERE slot_datetime LIKE ? AND reminded = 0 AND status = 'confirmed'
                ''', (f"{target_date_str}%",))
                
                upcoming = c.fetchall()
                
                for appt in upcoming:
                    user = db_get_user(appt["user_chat_id"])
                    if user:
                        msg = decorate_with_name(
                            f"یادآوری: فردا ساعت {appt['slot_datetime'].split(' ')[1]} نوبت {appt['service']} دارید.",
                            user["name"], user["lang"]
                        )
                        await send_message(user["chat_id"], msg)
                        
                        # Mark as reminded
                        c.execute("UPDATE appointments SET reminded = 1 WHERE id = ?", (appt["id"],))
                        conn.commit()
                        
        except Exception as e:
            logger.error(f"Scheduler Error: {e}")
            
        await asyncio.sleep(3600) # Check every hour

# -----------------------------------------
# DATABASE HELPERS
# -----------------------------------------
def db_upsert_user(chat_id: int, name=None, phone=None, lang=None):
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE chat_id=?", (chat_id,))
        if not c.fetchone():
            c.execute("INSERT INTO users (chat_id, name, phone, lang) VALUES (?, ?, ?, ?)", 
                      (chat_id, name, phone, lang or 'fa'))
        else:
            fields, values = [], []
            if name: fields.append("name = ?"); values.append(name)
            if phone: fields.append("phone = ?"); values.append(phone)
            if lang: fields.append("lang = ?"); values.append(lang)
            if fields:
                values.append(chat_id)
                c.execute(f"UPDATE users SET {', '.join(fields)} WHERE chat_id = ?", tuple(values))
        conn.commit()

def db_get_user(chat_id: int):
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE chat_id=?", (chat_id,))
        row = c.fetchone()
        return dict(row) if row else None

def db_check_slot_availability(datetime_str: str):
    """Check if a specific slot is already taken."""
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT id FROM appointments WHERE slot_datetime = ? AND status = 'confirmed'", (datetime_str,))
        return c.fetchone() is None # True if available

def db_book_slot(chat_id, service, doctor, datetime_str):
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("INSERT INTO appointments (user_chat_id, service, doctor, slot_datetime) VALUES (?, ?, ?, ?)",
                  (chat_id, service, doctor, datetime_str))
        conn.commit()

def db_get_all_users():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT chat_id FROM users")
        return [row[0] for row in c.fetchall()]

# -----------------------------------------
# TELEGRAM HELPERS
# -----------------------------------------
async def send_message(chat_id: int, text: str, reply_markup: dict = None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup: payload["reply_markup"] = reply_markup
    
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            await client.post(f"{TELEGRAM_URL}/sendMessage", json=payload)
        except Exception as e:
            logger.error(f"Send Msg Error: {e}")

async def get_telegram_file(file_id: str) -> bytes:
    """Downloads file from Telegram servers."""
    async with httpx.AsyncClient(timeout=30) as client:
        # 1. Get File Path
        r = await client.post(f"{TELEGRAM_URL}/getFile", json={"file_id": file_id})
        if r.status_code != 200: return None
        file_path = r.json()["result"]["file_path"]
        
        # 2. Download Content
        r_file = await client.get(f"{TELEGRAM_FILE_URL}/{file_path}")
        return r_file.content

# -----------------------------------------
# KEYBOARDS
# -----------------------------------------
def language_keyboard():
    return {
        "keyboard": [[{"text": "فارسی"}, {"text": "English"}]],
        "resize_keyboard": True, "one_time_keyboard": True
    }

def request_contact_keyboard(lang="fa"):
    text = "ارسال شماره تلفن (تایید هویت)" if lang == "fa" else "Share Phone Number (Verify)"
    return {
        "keyboard": [[{"text": text, "request_contact": True}]],
        "resize_keyboard": True, "one_time_keyboard": True
    }

def main_keyboard(lang="fa"):
    # Localized main menu
    if lang == "fa":
        return {
            "keyboard": [
                [{"text": "خدمات"}, {"text": "رزرو نوبت"}],
                [{"text": "ارسال عکس دندان"}, {"text": "ساعات کاری"}],
                [{"text": "آدرس مرکز"}]
            ], "resize_keyboard": True
        }
    else:
        return {
            "keyboard": [
                [{"text": "Services"}, {"text": "Book Appointment"}],
                [{"text": "Teledentistry (Photo)"}, {"text": "Working Hours"}],
                [{"text": "Location"}]
            ], "resize_keyboard": True
        }

def slots_keyboard(available_slots):
    """Generates buttons for available time slots."""
    buttons = []
    row = []
    for slot in available_slots:
        row.append({"text": slot})
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row: buttons.append(row)
    buttons.append([{"text": "Cancel / لغو"}])
    return {"keyboard": buttons, "resize_keyboard": True, "one_time_keyboard": True}

# -----------------------------------------
# GEMINI AI (TEXT & VISION)
# -----------------------------------------
async def ask_gemini(prompt: str, image_bytes: bytes = None, context_text: str = "") -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    
    parts = [{"text": context_text + "\n" + prompt}]
    
    if image_bytes:
        b64_data = base64.b64encode(image_bytes).decode("utf-8")
        parts.append({
            "inline_data": {
                "mime_type": "image/jpeg", 
                "data": b64_data
            }
        })

    body = {"contents": [{"parts": parts}]}
    headers = {"Content-Type": "application/json", "x-goog-api-key": GOOGLE_API_KEY}

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(url, headers=headers, json=body)
            if r.status_code != 200:
                logger.error(f"Gemini Error: {r.text}")
                return "Error processing request."
            return r.json()["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        logger.error(f"AI Exception: {e}")
        return "متأسفانه سیستم هوش مصنوعی در حال حاضر پاسخگو نیست."

def decorate_with_name(text, name, lang):
    if not name: return text
    prefix = f"{name} عزیز، " if lang == "fa" else f"Dear {name}, "
    return prefix + text

# -----------------------------------------
# STATE MANAGEMENT
# -----------------------------------------
REG_STATE = {}      # chat_id -> {step, temp_lang, temp_name}
BOOKING_STATE = {}  # chat_id -> {step, service, doctor, date_str}

# -----------------------------------------
# ROUTES & LOGIC
# -----------------------------------------
@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    
    if not chat_id: return {"ok": True}

    text = message.get("text", "").strip()
    contact = message.get("contact")
    photo = message.get("photo")

    user_data = db_get_user(chat_id)
    
    # --- ADMIN BROADCAST ---
    if text.startswith("/broadcast") and str(chat_id) == ADMIN_CHAT_ID:
        broadcast_msg = text.replace("/broadcast", "").strip()
        if not broadcast_msg:
            await send_message(chat_id, "Usage: /broadcast <message>")
            return {"ok": True}
        
        all_users = db_get_all_users()
        count = 0
        for uid in all_users:
            try:
                await send_message(uid, f"📢 پیام از مدیریت:\n\n{broadcast_msg}")
                count += 1
            except: pass
        await send_message(chat_id, f"پیام به {count} کاربر ارسال شد.")
        return {"ok": True}

    # --- START ---
    if text == "/start":
        REG_STATE[chat_id] = {"step": "lang"}
        BOOKING_STATE.pop(chat_id, None)
        await send_message(chat_id, "Welcome! Please select language / لطفاً زبان را انتخاب کنید:", reply_markup=language_keyboard())
        return {"ok": True}

    # --- REGISTRATION FLOW ---
    if chat_id in REG_STATE:
        state = REG_STATE[chat_id]
        
        if state["step"] == "lang":
            lang = "fa" if "فارسی" in text else "en"
            state["temp_lang"] = lang
            state["step"] = "name"
            msg = "لطفاً نام کامل خود را بنویسید:" if lang == "fa" else "Please enter your full name:"
            await send_message(chat_id, msg)
            return {"ok": True}
            
        if state["step"] == "name":
            state["temp_name"] = text
            state["step"] = "phone"
            lang = state["temp_lang"]
            # SECURITY FEATURE: Force Request Contact Button
            msg = "لطفاً دکمه زیر را بزنید تا شماره شما تایید شود:" if lang == "fa" else "Please tap the button below to verify your phone:"
            await send_message(chat_id, msg, reply_markup=request_contact_keyboard(lang))
            return {"ok": True}

        if state["step"] == "phone":
            # Must receive Contact object
            if not contact:
                msg = "لطفاً فقط از دکمه پایین استفاده کنید." if state["temp_lang"] == "fa" else "Please use the button below."
                await send_message(chat_id, msg, reply_markup=request_contact_keyboard(state["temp_lang"]))
                return {"ok": True}
            
            phone_number = contact.get("phone_number")
            db_upsert_user(chat_id, state["temp_name"], phone_number, state["temp_lang"])
            REG_STATE.pop(chat_id, None)
            
            welcome = "ثبت‌نام تکمیل شد. منوی خدمات:" if state["temp_lang"] == "fa" else "Registration complete. Main Menu:"
            await send_message(chat_id, decorate_with_name(welcome, state["temp_name"], state["temp_lang"]), 
                               reply_markup=main_keyboard(state["temp_lang"]))
            return {"ok": True}

    if not user_data:
        await send_message(chat_id, "Please /start to register.")
        return {"ok": True}

    user_name = user_data["name"]
    user_lang = user_data["lang"]

    # --- TELEDENTISTRY (IMAGE ANALYSIS) ---
    if photo:
        # Get largest photo
        file_id = photo[-1]["file_id"]
        await send_message(chat_id, "در حال تحلیل تصویر... 🤖" if user_lang == "fa" else "Analyzing image... 🤖")
        
        image_bytes = await get_telegram_file(file_id)
        if image_bytes:
            ai_response = await ask_gemini(
                "Analyze this dental image. Briefly explain what you see (cavity, gum issue, etc) and suggest if they need a visit. DISCLAIMER: Not medical advice.", 
                image_bytes, 
                f"User: {user_name}, Lang: {user_lang}"
            )
            await send_message(chat_id, decorate_with_name(ai_response, user_name, user_lang), reply_markup=main_keyboard(user_lang))
        return {"ok": True}

    # --- BOOKING FLOW (SMART SLOTS) ---
    if chat_id in BOOKING_STATE:
        state = BOOKING_STATE[chat_id]
        
        if text.lower() in ["cancel", "لغو"]:
            del BOOKING_STATE[chat_id]
            await send_message(chat_id, "Cancelled.", reply_markup=main_keyboard(user_lang))
            return {"ok": True}

        if state["step"] == "service":
            state["service"] = text
            state["step"] = "date_select"
            # Simplify: Assume 2 days availability for demo
            # In real app: Ask Date first. Here we jump to slots for "Tomorrow"
            state["target_date"] = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
            
            # Generate Slots (10:00 to 20:00)
            possible_slots = [f"{h}:00" for h in range(10, 21)]
            available_slots = []
            
            for slot in possible_slots:
                dt_str = f"{state['target_date']} {slot}"
                if db_check_slot_availability(dt_str):
                    available_slots.append(dt_str)
            
            if not available_slots:
                await send_message(chat_id, "متأسفانه برای فردا وقت خالی نداریم.", reply_markup=main_keyboard(user_lang))
                del BOOKING_STATE[chat_id]
                return {"ok": True}
            
            state["step"] = "slot_pick"
            msg = "لطفاً یکی از زمان‌های خالی فردا را انتخاب کنید:" if user_lang == "fa" else "Select an available slot for tomorrow:"
            await send_message(chat_id, msg, reply_markup=slots_keyboard(available_slots))
            return {"ok": True}

        if state["step"] == "slot_pick":
            # Validate slot format
            if text not in ["Cancel / لغو"] and ":" in text:
                # Double check availability (Race condition check)
                if db_check_slot_availability(text):
                    db_book_slot(chat_id, state["service"], "General Dentist", text)
                    
                    success_msg = f"نوبت شما برای {text} ثبت شد.\nخدمت: {state['service']}" if user_lang == "fa" else f"Booked for {text}."
                    await send_message(chat_id, decorate_with_name(success_msg, user_name, user_lang), reply_markup=main_keyboard(user_lang))
                    
                    # Notify Admin
                    if ADMIN_CHAT_ID:
                        try:
                            await send_message(int(ADMIN_CHAT_ID), f"📅 رزرو جدید:\n{user_name}\n{user_data['phone']}\n{text}")
                        except: pass
                else:
                    await send_message(chat_id, "این نوبت همین الان پر شد! لطفاً زمان دیگری انتخاب کنید.")
                    return {"ok": True}
                
                del BOOKING_STATE[chat_id]
                return {"ok": True}

    # --- MENU HANDLERS ---
    if text in ["رزرو نوبت", "Book Appointment"]:
        BOOKING_STATE[chat_id] = {"step": "service"}
        msg = "چه خدمتی نیاز دارید؟ (چکاپ، ایمپلنت...)" if user_lang == "fa" else "Which service?"
        await send_message(chat_id, msg)
        return {"ok": True}
    
    if text in ["ارسال عکس دندان", "Teledentistry (Photo)"]:
        msg = "لطفاً یک عکس واضح از دندان خود بگیرید و ارسال کنید." if user_lang == "fa" else "Please send a clear photo of your teeth."
        await send_message(chat_id, msg)
        return {"ok": True}

    # --- FALLBACK AI ---
    ai_resp = await ask_gemini(text, context_text=f"User: {user_name}, Lang: {user_lang}")
    await send_message(chat_id, decorate_with_name(ai_resp, user_name, user_lang), reply_markup=main_keyboard(user_lang))
    
    return {"ok": True}
