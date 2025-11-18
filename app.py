import os
import sqlite3
import json
import base64
import asyncio
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, Request
import httpx
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

# -----------------------------------------
# CONFIG
# -----------------------------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
DB_NAME = "dental_bot.db"

# تنظیم منطقه زمانی دبی (UTC+4)
DUBAI_TZ = timezone(timedelta(hours=4))

if not TELEGRAM_TOKEN or not GOOGLE_API_KEY:
    raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or GOOGLE_API_KEY")

# -----------------------------------------
# TEXTS
# -----------------------------------------
TRANS = {
    "fa": {
        "buttons": [["خدمات", "ساعات کاری"], ["رزرو نوبت", "آدرس مرکز"], ["سوال از منشی"]],
        "share_contact": "📱 ارسال شماره تماس (تأیید هویت)",
        "reg_contact_prompt": "لطفاً برای تکمیل ثبت‌نام، روی دکمه زیر بزنید تا شماره شما تأیید شود:",
        "use_button_error": "⛔️ لطفاً شماره را تایپ نکنید. حتماً از دکمه «ارسال شماره تماس» در پایین صفحه استفاده کنید.",
        "booking_prompt": "چه خدمتی نیاز دارید؟",
        "doctor_prompt": "نام دکتر (یا بنویسید 'فرقی نمی‌کند'):",
        "time_prompt": "لطفاً یکی از زمان‌های خالی زیر را انتخاب کنید (زمان به وقت دبی):",
        "photo_analyzing": "🖼 در حال بررسی تصویر دندان شما توسط هوش مصنوعی... لطفاً صبر کنید.",
        "photo_disclaimer": "\n\n⚠️ توجه: این فقط یک تحلیل اولیه هوشمند است و جایگزین تشخیص پزشک نیست.",
        "reminder_msg": "{name} عزیز، یادآوری: شما فردا ({date}) ساعت {time} نوبت دندانپزشکی دارید.",
        "file_too_large": "⚠️ حجم فایل ارسالی زیاد است. لطفاً عکس کم‌حجم‌تر بفرستید.",
        "slot_taken": "متأسفانه این زمان پر شد. لطفاً زمان دیگری انتخاب کنید.",
        "no_slots": "در حال حاضر وقت خالی برای ۷ روز آینده موجود نیست. لطفاً تماس بگیرید.",
        "booking_done": "✅ نوبت شما رزرو شد.",
        "reg_complete": "ثبت‌نام انجام شد. خوش آمدید 🌹",
        "cancelled": "لغو شد."
    },
    "en": {
        "buttons": [["Services", "Working Hours"], ["Book Appointment", "Location"], ["Ask Receptionist"]],
        "share_contact": "📱 Share Contact",
        "reg_contact_prompt": "Please tap the button below to share your verified phone number:",
        "use_button_error": "⛔️ Please do not type. Use the 'Share Contact' button below.",
        "booking_prompt": "Which service?",
        "doctor_prompt": "Doctor name (or 'Any'):",
        "time_prompt": "Please select a slot (Dubai Time):",
        "photo_analyzing": "🖼 Analyzing your dental image...",
        "photo_disclaimer": "\n\n⚠️ Note: AI analysis is not a medical diagnosis.",
        "reminder_msg": "Dear {name}, Reminder: You have an appointment tomorrow ({date}) at {time}.",
        "file_too_large": "⚠️ File too large.",
        "slot_taken": "Slot taken. Choose another.",
        "no_slots": "No slots available for the next 7 days.",
        "booking_done": "✅ Appointment confirmed.",
        "reg_complete": "Registration complete. Welcome!",
        "cancelled": "Cancelled."
    }
}

# -----------------------------------------
# DATABASE & LOGIC
# -----------------------------------------
def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        # فعال‌سازی WAL برای مدیریت بهتر همزمانی
        conn.execute("PRAGMA journal_mode=WAL;")
        
        conn.execute("CREATE TABLE IF NOT EXISTS users (chat_id INTEGER PRIMARY KEY, name TEXT, phone TEXT, lang TEXT DEFAULT 'fa')")
        conn.execute("CREATE TABLE IF NOT EXISTS states (chat_id INTEGER PRIMARY KEY, flow_type TEXT, step TEXT, data TEXT)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS slots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                datetime_str TEXT UNIQUE, 
                is_booked INTEGER DEFAULT 0,
                booked_by INTEGER,
                reminder_sent INTEGER DEFAULT 0
            )
        """)
        conn.commit()
    
    # بررسی و تولید اسلات‌ها در هر بار اجرا
    ensure_future_slots()

def get_dubai_now():
    return datetime.now(DUBAI_TZ)

def ensure_future_slots():
    """تضمین می‌کند که همیشه برای ۷ روز آینده اسلات خالی وجود دارد"""
    with sqlite3.connect(DB_NAME) as conn:
        now = get_dubai_now()
        count_added = 0
        # برای ۷ روز آینده
        for day in range(1, 8):
            date = now + timedelta(days=day)
            # ساعات کاری: ۱۰ تا ۲۰ (هر دو ساعت)
            for hour in [10, 12, 14, 16, 18, 20]:
                dt_str = f"{date.strftime('%Y-%m-%d')} {hour:02d}:00"
                try:
                    conn.execute("INSERT INTO slots (datetime_str) VALUES (?)", (dt_str,))
                    count_added += 1
                except sqlite3.IntegrityError:
                    pass # اسلات قبلاً وجود دارد
        if count_added > 0:
            conn.commit()
            print(f"Added {count_added} new slots.")
        
        # پاکسازی اسلات‌های قدیمی (دیروز و قبل‌تر)
        yesterday = (now - timedelta(days=1)).strftime('%Y-%m-%d')
        conn.execute("DELETE FROM slots WHERE datetime_str < ?", (yesterday,))
        conn.commit()

# --- DB HELPERS ---
def upsert_user(chat_id, name=None, phone=None, lang=None):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.execute("SELECT * FROM users WHERE chat_id=?", (chat_id,))
        if cursor.fetchone():
            q, p = "UPDATE users SET ", []
            if name: q+="name=?, "; p.append(name)
            if phone: q+="phone=?, "; p.append(phone)
            if lang: q+="lang=?, "; p.append(lang)
            if p: conn.execute(q.rstrip(", ")+" WHERE chat_id=?", (*p, chat_id))
        else:
            conn.execute("INSERT INTO users (chat_id, name, phone, lang) VALUES (?,?,?,?)", (chat_id, name, phone, lang or 'fa'))
        conn.commit()

def get_user(chat_id):
    with sqlite3.connect(DB_NAME) as conn:
        return conn.execute("SELECT name, phone, lang FROM users WHERE chat_id=?", (chat_id,)).fetchone()

def get_all_users():
    with sqlite3.connect(DB_NAME) as conn:
        return [r[0] for r in conn.execute("SELECT chat_id FROM users").fetchall()]

def get_available_slots():
    ensure_future_slots() # اطمینان از وجود اسلات قبل از نمایش
    with sqlite3.connect(DB_NAME) as conn:
        now_str = get_dubai_now().strftime("%Y-%m-%d %H:%M")
        # ۱۰ اسلات بعدی خالی را برگردان
        return [r[0] for r in conn.execute("SELECT datetime_str FROM slots WHERE is_booked=0 AND datetime_str > ? ORDER BY datetime_str ASC LIMIT 10", (now_str,)).fetchall()]

def book_slot_atomic(dt_str, chat_id):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.execute("UPDATE slots SET is_booked=1, booked_by=? WHERE datetime_str=? AND is_booked=0", (chat_id, dt_str))
        conn.commit()
        return cursor.rowcount > 0

def get_pending_reminders():
    # یادآوری برای نوبت‌های "فردا"
    tomorrow = (get_dubai_now() + timedelta(days=1)).strftime("%Y-%m-%d")
    with sqlite3.connect(DB_NAME) as conn:
        q = """
            SELECT slots.id, slots.datetime_str, users.chat_id, users.name, users.lang 
            FROM slots 
            JOIN users ON slots.booked_by = users.chat_id 
            WHERE is_booked=1 AND reminder_sent=0 AND datetime_str LIKE ?
        """
        return conn.execute(q, (f"{tomorrow}%",)).fetchall()

def mark_reminder_as_sent(slot_id):
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("UPDATE slots SET reminder_sent=1 WHERE id=?", (slot_id,))
        conn.commit()

# -----------------------------------------
# TELEGRAM & AI
# -----------------------------------------
async def send_message(chat_id: int, text: str, reply_markup: dict = None):
    try:
        async with httpx.AsyncClient() as client:
            await client.post(f"{TELEGRAM_URL}/sendMessage", json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "reply_markup": reply_markup})
    except Exception as e: print(f"Send Error: {e}")

async def get_file_info(file_id):
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{TELEGRAM_URL}/getFile?file_id={file_id}")
            return r.json().get("result")
    except: return None

async def analyze_image_with_gemini(file_path, caption, lang):
    file_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            img_data = (await client.get(file_url)).content
        
        b64_img = base64.b64encode(img_data).decode("utf-8")
        prompt = "Analyze this dental image. Identify issues. Be professional. This is NOT a diagnosis."
        if lang == "fa": prompt += " Answer in Persian."
        
        url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"
        body = {
            "contents": [{
                "parts": [
                    {"text": f"{prompt}\nUser Question: {caption}"},
                    {"inline_data": {"mime_type": "image/jpeg", "data": b64_img}}
                ]
            }]
        }
        async with httpx.AsyncClient(timeout=45) as client:
            r = await client.post(url, headers={"Content-Type": "application/json", "x-goog-api-key": GOOGLE_API_KEY}, json=body)
            return r.json()["candidates"][0]["content"]["parts"][0]["text"]
    except: return "Error analyzing image."

def contact_keyboard(lang):
    text = TRANS.get(lang, TRANS["en"])["share_contact"]
    return {"keyboard": [[{"text": text, "request_contact": True}]], "resize_keyboard": True, "one_time_keyboard": True}

def main_keyboard(lang):
    btns = TRANS.get(lang, TRANS["en"])["buttons"]
    return {"keyboard": [[{"text": b} for b in r] for r in btns], "resize_keyboard": True}

def slots_keyboard(slots):
    kb = []
    row = []
    for s in slots:
        # نمایش: 2023-11-20 10:00 -> 11-20 10:00
        display = s[5:]
        row.append({"text": display})
        if len(row) == 2: kb.append(row); row=[]
    if row: kb.append(row)
    kb.append([{"text": "Cancel"}])
    return {"keyboard": kb, "resize_keyboard": True}

# -----------------------------------------
# ROUTES
# -----------------------------------------
@app.on_event("startup")
def startup_event():
    init_db() # اطمینان از ساخت دیتابیس و اسلات‌ها هنگام روشن شدن سرور

@app.get("/")
async def root(): return {"status": "ok", "message": "Dental Bot V5 (Golden Release)"}

@app.get("/trigger-reminders")
async def trigger_reminders():
    reminders = get_pending_reminders()
    count = 0
    for slot_id, dt_str, chat_id, name, lang in reminders:
        texts = TRANS.get(lang, TRANS["en"])
        date_part = dt_str.split(" ")[0]
        time_part = dt_str.split(" ")[1]
        msg = texts["reminder_msg"].format(name=name, date=date_part, time=time_part)
        await send_message(chat_id, "⏰ " + msg)
        mark_reminder_as_sent(slot_id)
        count += 1
    return {"status": "success", "sent": count}

@app.post("/webhook")
async def webhook(request: Request):
    try: data = await request.json()
    except: return {"ok": True}
    
    msg = data.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    text = (msg.get("text") or "").strip()
    
    if not chat_id: return {"ok": True}

    # --- ADMIN BROADCAST ---
    if str(chat_id) == str(ADMIN_CHAT_ID) and text.startswith("/broadcast"):
        body = text.replace("/broadcast", "").strip()
        if body:
            users = get_all_users()
            for u in users: await send_message(u, "📢 " + body)
            await send_message(chat_id, f"Done. Sent to {len(users)}.")
        return {"ok": True}

    # Load User & State
    with sqlite3.connect(DB_NAME) as conn:
        state_row = conn.execute("SELECT flow_type, step, data FROM states WHERE chat_id=?", (chat_id,)).fetchone()
        current_state = {"flow_type": state_row[0], "step": state_row[1], "data": json.loads(state_row[2])} if state_row else None
    
    user_row = get_user(chat_id)
    lang = user_row[2] if user_row else "en"
    texts = TRANS.get(lang, TRANS["en"])

    # --- IMAGE HANDLING ---
    if msg.get("photo"):
        if not user_row:
            await send_message(chat_id, "Please register first.")
            return {"ok": True}
        
        f_size = msg["photo"][-1].get("file_size", 0)
        if f_size > 19 * 1024 * 1024:
            await send_message(chat_id, texts["file_too_large"])
            return {"ok": True}
            
        await send_message(chat_id, texts["photo_analyzing"])
        f_info = await get_file_info(msg["photo"][-1]["file_id"])
        if f_info:
            res = await analyze_image_with_gemini(f_info["file_path"], msg.get("caption", ""), lang)
            await send_message(chat_id, "🦷 **AI:**\n" + res + texts["photo_disclaimer"], reply_markup=main_keyboard(lang))
        return {"ok": True}

    # --- CONTACT VERIFICATION ---
    if current_state and current_state["step"] == "phone":
        if msg.get("contact"):
            contact = msg["contact"]
            if contact.get("user_id") != chat_id:
                await send_message(chat_id, "Error: Not your contact.", reply_markup=contact_keyboard(lang))
                return {"ok": True}
            
            data = current_state["data"]
            upsert_user(chat_id, name=data["name"], phone=contact.get("phone_number"), lang=data["lang"])
            with sqlite3.connect(DB_NAME) as conn: conn.execute("DELETE FROM states WHERE chat_id=?", (chat_id,)); conn.commit()
            
            welcome_msg = TRANS.get(data["lang"], TRANS["en"])["reg_complete"]
            await send_message(chat_id, welcome_msg, reply_markup=main_keyboard(data["lang"]))
        else:
            # کاربر متن فرستاده در حالی که باید دکمه را می‌زد
            err = TRANS.get(current_state["data"]["lang"], TRANS["en"])["use_button_error"]
            await send_message(chat_id, err, reply_markup=contact_keyboard(current_state["data"]["lang"]))
        return {"ok": True}

    # --- START ---
    if text == "/start":
        with sqlite3.connect(DB_NAME) as conn:
            conn.execute("DELETE FROM states WHERE chat_id=?", (chat_id,))
            conn.execute("INSERT INTO states (chat_id, flow_type, step, data) VALUES (?,?,?,?)", (chat_id, "reg", "lang", "{}"))
            conn.commit()
        kb = {"keyboard": [[{"text": "فارسی"}, {"text": "English"}]], "resize_keyboard": True}
        await send_message(chat_id, "Language / زبان:", reply_markup=kb)
        return {"ok": True}

    # --- REGISTRATION ---
    if current_state and current_state["flow_type"] == "reg":
        step = current_state["step"]
        data = current_state["data"]

        if step == "lang":
            sel_lang = "fa" if "فارسی" in text else "en"
            with sqlite3.connect(DB_NAME) as conn:
                conn.execute("UPDATE states SET step=?, data=? WHERE chat_id=?", ("name", json.dumps({"lang": sel_lang}), chat_id))
                conn.commit()
            msg = "نام خود را وارد کنید:" if sel_lang=="fa" else "Enter your name:"
            await send_message(chat_id, msg)
            return {"ok": True}

        if step == "name":
            data["name"] = text
            with sqlite3.connect(DB_NAME) as conn:
                conn.execute("UPDATE states SET step=?, data=? WHERE chat_id=?", ("phone", json.dumps(data), chat_id))
                conn.commit()
            p_msg = TRANS.get(data["lang"], TRANS["en"])["reg_contact_prompt"]
            await send_message(chat_id, p_msg, reply_markup=contact_keyboard(data["lang"]))
            return {"ok": True}

    if not user_row:
        await send_message(chat_id, "Type /start to register.")
        return {"ok": True}

    # --- BOOKING ---
    if current_state and current_state["flow_type"] == "booking":
        step = current_state["step"]
        data = current_state["data"]
        
        if "cancel" in text.lower() or "لغو" in text:
             with sqlite3.connect(DB_NAME) as conn: conn.execute("DELETE FROM states WHERE chat_id=?", (chat_id,)); conn.commit()
             await send_message(chat_id, texts["cancelled"], reply_markup=main_keyboard(lang))
             return {"ok": True}
             
        if step == "service":
            data["service"] = text
            with sqlite3.connect(DB_NAME) as conn: conn.execute("UPDATE states SET step=?, data=? WHERE chat_id=?", ("doctor", json.dumps(data), chat_id)); conn.commit()
            await send_message(chat_id, texts["doctor_prompt"])
            return {"ok": True}

        if step == "doctor":
            data["doctor"] = text
            slots = get_available_slots()
            if not slots:
                with sqlite3.connect(DB_NAME) as conn: conn.execute("DELETE FROM states WHERE chat_id=?", (chat_id,)); conn.commit()
                await send_message(chat_id, texts["no_slots"], reply_markup=main_keyboard(lang))
                return {"ok": True}
            
            with sqlite3.connect(DB_NAME) as conn: conn.execute("UPDATE states SET step=?, data=? WHERE chat_id=?", ("slot", json.dumps(data), chat_id)); conn.commit()
            await send_message(chat_id, texts["time_prompt"], reply_markup=slots_keyboard(slots))
            return {"ok": True}

        if step == "slot":
            # تلاش برای پیدا کردن نوبت کامل از متن کوتاه دکمه
            # کاربر: "11-20 10:00" -> دیتابیس: "2025-11-20 10:00"
            short_txt = text
            full_slot = None
            # برای دقت بیشتر، دوباره اسلات‌ها را می‌گیریم و چک می‌کنیم کدام با متن کاربر مچ می‌شود
            possible_slots = get_available_slots() 
            # اما این متد فقط ۱۰ تا را برمی‌گرداند. بهتر است مستقیم کوئری بزنیم
            # برای امنیت، فرض می‌کنیم اسلات معتبر باید در دیتابیس باشد
            with sqlite3.connect(DB_NAME) as conn:
                # جستجوی اسلاتی که به متن کاربر ختم می‌شود
                found = conn.execute("SELECT datetime_str FROM slots WHERE datetime_str LIKE ? AND is_booked=0", (f"%{short_txt}",)).fetchone()
                if found: full_slot = found[0]

            if full_slot and book_slot_atomic(full_slot, chat_id):
                with sqlite3.connect(DB_NAME) as conn: conn.execute("DELETE FROM states WHERE chat_id=?", (chat_id,)); conn.commit()
                await send_message(chat_id, texts["booking_done"], reply_markup=main_keyboard(lang))
                if ADMIN_CHAT_ID:
                    try: await send_message(int(ADMIN_CHAT_ID), f"📅 Booking:\n{user_row[0]}\n{full_slot}\n{data.get('service')}")
                    except: pass
            else:
                # رفرش کردن لیست برای کاربر
                new_slots = get_available_slots()
                await send_message(chat_id, texts["slot_taken"], reply_markup=slots_keyboard(new_slots))
            return {"ok": True}

    # --- MAIN MENU ---
    flat_btns = [b for r in texts["buttons"] for b in r]
    if text in flat_btns:
        idx = flat_btns.index(text)
        if idx == 0: # Services
            await send_message(chat_id, texts["services_reply"], reply_markup=main_keyboard(lang))
        elif idx == 1: # Hours
            await send_message(chat_id, texts["hours_reply"], reply_markup=main_keyboard(lang))
        elif idx == 2: # Book
             with sqlite3.connect(DB_NAME) as conn: conn.execute("INSERT OR REPLACE INTO states (chat_id, flow_type, step, data) VALUES (?,?,?,?)", (chat_id, "booking", "service", "{}")); conn.commit()
             await send_message(chat_id, texts["booking_prompt"])
        elif idx == 3: # Address
             await send_message(chat_id, texts["address_reply"], reply_markup=main_keyboard(lang))
        elif idx == 4: # Ask
             await send_message(chat_id, "Please ask your question / سوال خود را بپرسید:", reply_markup=main_keyboard(lang))
        return {"ok": True}

    # --- AI CHAT (TEXT) ---
    if user_row:
        # فراخوانی هوش مصنوعی برای متن (پیاده‌سازی ساده با همان تابع عکس اما بدون تصویر)
        # برای خلاصه نگه داشتن کد، اینجا می‌توان همان تابع analyze_image را با تغییرات کوچک استفاده کرد
        # یا یک تابع جداگانه ask_gemini_text داشت.
        # اینجا برای تکمیل بودن، یک پاسخ جنریک می‌دهم، اما شما می‌توانید تابع ask_gemini قبلی را اینجا بگذارید.
        async with httpx.AsyncClient() as client:
            url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"
            body = {"contents": [{"parts": [{"text": f"You are a dental receptionist. User asks: {text}. Answer in {lang}."}]}]}
            try:
                r = await client.post(url, headers={"Content-Type": "application/json", "x-goog-api-key": GOOGLE_API_KEY}, json=body)
                ans = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                await send_message(chat_id, ans, reply_markup=main_keyboard(lang))
            except:
                await send_message(chat_id, texts["error"])

    return {"ok": True}
