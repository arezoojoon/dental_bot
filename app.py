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
# TEXTS & TRANSLATIONS
# -----------------------------------------
TRANS = {
    "fa": {
        "buttons": [["خدمات", "ساعات کاری"], ["رزرو نوبت", "آدرس مرکز"], ["سوال از منشی"]],
        "share_contact": "📱 ارسال شماره تماس (تأیید هویت)",
        "name_prompt": "لطفاً نام و نام خانوادگی خود را وارد کنید:",
        "whatsapp_prompt": "لطفاً شماره واتساپ خود را بنویسید (مثال: 0912...):",
        "phone_prompt": "اکنون برای تکمیل نهایی، روی دکمه زیر بزنید تا شماره تلگرام شما تأیید شود:",
        "use_button_error": "⛔️ لطفاً تایپ نکنید. از دکمه «ارسال شماره تماس» استفاده کنید.",
        "reg_complete": "ثبت‌نام کامل شد. خوش آمدید 🌹",
        "greeting": "{name} عزیز، ",
        "services_reply": "خدمات ما:\n• ایمپلنت\n• ارتودنسی\n• لمینت\n• جرمگیری\n• عصب‌کشی",
        "hours_reply": "ساعات کاری:\nهمه روزه ۱۰ صبح تا ۹ شب",
        "address_reply": "آدرس:\nدبی، خیابان الوصل، الصفا ۱",
        "booking_prompt": "چه خدمتی نیاز دارید؟",
        "doctor_prompt": "نام دکتر (یا بنویسید 'فرقی نمی‌کند'):",
        "time_prompt": "لطفاً یکی از زمان‌های خالی زیر را انتخاب کنید (زمان به وقت دبی):",
        "booking_done": "✅ نوبت شما رزرو شد.",
        "photo_analyzing": "🖼 در حال بررسی تصویر...",
        "photo_disclaimer": "\n\n⚠️ توجه: این فقط یک تحلیل هوشمند است و جایگزین پزشک نیست.",
        "file_too_large": "⚠️ حجم فایل زیاد است.",
        "slot_taken": "متأسفانه این زمان پر شد.",
        "no_slots": "وقت خالی موجود نیست.",
        "cancelled": "لغو شد."
    },
    "en": {
        "buttons": [["Services", "Working Hours"], ["Book Appointment", "Location"], ["Ask Receptionist"]],
        "share_contact": "📱 Share Contact",
        "name_prompt": "Please enter your full name:",
        "whatsapp_prompt": "Please enter your WhatsApp number:",
        "phone_prompt": "Now please tap the button below to verify your Telegram phone number:",
        "use_button_error": "⛔️ Please use the 'Share Contact' button.",
        "reg_complete": "Registration complete. Welcome!",
        "greeting": "Dear {name}, ",
        "services_reply": "Our Services:\n• Implants\n• Orthodontics\n• Veneers\n• Scaling",
        "hours_reply": "Working Hours:\nDaily 10:00 AM - 09:00 PM",
        "address_reply": "Address:\nDubai, Al Wasl Rd, Al Safa 1",
        "booking_prompt": "Which service?",
        "doctor_prompt": "Doctor name (or 'Any'):",
        "time_prompt": "Please select a slot (Dubai Time):",
        "booking_done": "✅ Appointment confirmed.",
        "photo_analyzing": "🖼 Analyzing image...",
        "photo_disclaimer": "\n\n⚠️ Note: Not a medical diagnosis.",
        "file_too_large": "⚠️ File too large.",
        "slot_taken": "Slot taken.",
        "no_slots": "No slots available.",
        "cancelled": "Cancelled."
    },
    "ar": {
        "buttons": [["الخدمات", "ساعات العمل"], ["حجز موعد", "العنوان"], ["سؤال الاستقبال"]],
        "share_contact": "📱 مشاركة رقم الهاتف",
        "name_prompt": "الرجاء إدخال اسمك الكامل:",
        "whatsapp_prompt": "الرجاء إدخال رقم الواتساب:",
        "phone_prompt": "الآن اضغط على الزر أدناه لتأكيد رقم هاتفك:",
        "use_button_error": "⛔️ الرجاء استخدام زر المشاركة.",
        "reg_complete": "تم التسجيل بنجاح. أهلاً بك!",
        "greeting": "عزيزي {name}، ",
        "services_reply": "خدماتنا:\n• زراعة الأسنان\n• تقويم الأسنان\n• القشور الخزفية",
        "hours_reply": "ساعات العمل:\nيومياً من ١٠ صباحاً حتى ٩ مساءً",
        "address_reply": "العنوان:\nدبي، شارع الوصل، الصفا ١",
        "booking_prompt": "ما هي الخدمة المطلوبة؟",
        "doctor_prompt": "اسم الطبيب (أو 'أي طبيب'):",
        "time_prompt": "اختر وقتاً (توقيت دبي):",
        "booking_done": "✅ تم الحجز.",
        "photo_analyzing": "🖼 جاري التحليل...",
        "photo_disclaimer": "\n\n⚠️ ملاحظة: هذا ليس تشخيصاً طبياً.",
        "file_too_large": "⚠️ الملف كبير جداً.",
        "slot_taken": "الموعد محجوز.",
        "no_slots": "لا توجد مواعيد.",
        "cancelled": "تم الإلغاء."
    },
    "ru": {
        "buttons": [["Услуги", "Часы работы"], ["Записаться", "Адрес"], ["Вопрос ресепшн"]],
        "share_contact": "📱 Отправить контакт",
        "name_prompt": "Введите ваше полное имя:",
        "whatsapp_prompt": "Введите номер WhatsApp:",
        "phone_prompt": "Нажмите кнопку ниже, чтобы подтвердить номер:",
        "use_button_error": "⛔️ Используйте кнопку отправки контакта.",
        "reg_complete": "Регистрация завершена. Добро пожаловать!",
        "greeting": "Уважаемый(ая) {name}, ",
        "services_reply": "Услуги:\n• Имплантация\n• Ортодонтия\n• Виниры",
        "hours_reply": "Часы работы:\nЕжедневно 10:00 - 21:00",
        "address_reply": "Адрес:\nДубай, Аль Васл Роуд",
        "booking_prompt": "Какая услуга?",
        "doctor_prompt": "Врач (или 'Любой'):",
        "time_prompt": "Выберите время:",
        "booking_done": "✅ Запись подтверждена.",
        "photo_analyzing": "🖼 Анализ...",
        "photo_disclaimer": "\n\n⚠️ Это не диагноз.",
        "file_too_large": "⚠️ Файл большой.",
        "slot_taken": "Занято.",
        "no_slots": "Нет мест.",
        "cancelled": "Отменено."
    }
}

# -----------------------------------------
# DATABASE & LOGIC
# -----------------------------------------
def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        # جدول کاربران: اضافه شدن ستون whatsapp
        conn.execute("CREATE TABLE IF NOT EXISTS users (chat_id INTEGER PRIMARY KEY, name TEXT, whatsapp TEXT, phone TEXT, lang TEXT DEFAULT 'fa')")
        try: conn.execute("ALTER TABLE users ADD COLUMN whatsapp TEXT") 
        except: pass # اگر ستون قبلاً بود خطا ندهد

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
    ensure_future_slots()

def get_dubai_now():
    return datetime.now(DUBAI_TZ)

def ensure_future_slots():
    with sqlite3.connect(DB_NAME) as conn:
        now = get_dubai_now()
        for day in range(1, 8):
            date = now + timedelta(days=day)
            for hour in [10, 12, 14, 16, 18, 20]:
                dt_str = f"{date.strftime('%Y-%m-%d')} {hour:02d}:00"
                try: conn.execute("INSERT INTO slots (datetime_str) VALUES (?)", (dt_str,))
                except: pass
        yesterday = (now - timedelta(days=1)).strftime('%Y-%m-%d')
        conn.execute("DELETE FROM slots WHERE datetime_str < ?", (yesterday,))
        conn.commit()

# تابع ذخیره/آپدیت کاربر
def upsert_user(chat_id, name=None, whatsapp=None, phone=None, lang=None):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.execute("SELECT * FROM users WHERE chat_id=?", (chat_id,))
        if cursor.fetchone():
            q, p = "UPDATE users SET ", []
            if name: q+="name=?, "; p.append(name)
            if whatsapp: q+="whatsapp=?, "; p.append(whatsapp)
            if phone: q+="phone=?, "; p.append(phone)
            if lang: q+="lang=?, "; p.append(lang)
            if p: conn.execute(q.rstrip(", ")+" WHERE chat_id=?", (*p, chat_id))
        else:
            conn.execute("INSERT INTO users (chat_id, name, whatsapp, phone, lang) VALUES (?,?,?,?,?)", 
                         (chat_id, name, whatsapp, phone, lang or 'fa'))
        conn.commit()

def get_user(chat_id):
    with sqlite3.connect(DB_NAME) as conn:
        # ترتیب: name, whatsapp, phone, lang
        return conn.execute("SELECT name, whatsapp, phone, lang FROM users WHERE chat_id=?", (chat_id,)).fetchone()

def get_all_users():
    with sqlite3.connect(DB_NAME) as conn:
        return [r[0] for r in conn.execute("SELECT chat_id FROM users").fetchall()]

def get_available_slots():
    ensure_future_slots()
    with sqlite3.connect(DB_NAME) as conn:
        now_str = get_dubai_now().strftime("%Y-%m-%d %H:%M")
        return [r[0] for r in conn.execute("SELECT datetime_str FROM slots WHERE is_booked=0 AND datetime_str > ? ORDER BY datetime_str ASC LIMIT 10", (now_str,)).fetchall()]

def book_slot_atomic(dt_str, chat_id):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.execute("UPDATE slots SET is_booked=1, booked_by=? WHERE datetime_str=? AND is_booked=0", (chat_id, dt_str))
        conn.commit()
        return cursor.rowcount > 0

def get_pending_reminders():
    tomorrow = (get_dubai_now() + timedelta(days=1)).strftime("%Y-%m-%d")
    with sqlite3.connect(DB_NAME) as conn:
        q = """SELECT slots.id, slots.datetime_str, users.chat_id, users.name, users.lang 
               FROM slots JOIN users ON slots.booked_by = users.chat_id 
               WHERE is_booked=1 AND reminder_sent=0 AND datetime_str LIKE ?"""
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
        prompt = "Analyze this dental image. Identify issues. Be professional. NOT a medical diagnosis."
        if lang == "fa": prompt += " Answer in Persian."
        elif lang == "ar": prompt += " Answer in Arabic."
        elif lang == "ru": prompt += " Answer in Russian."
        
        url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"
        body = {"contents": [{"parts": [{"text": f"{prompt}\nUser Question: {caption}"}, {"inline_data": {"mime_type": "image/jpeg", "data": b64_img}}]}]}
        async with httpx.AsyncClient(timeout=45) as client:
            r = await client.post(url, headers={"Content-Type": "application/json", "x-goog-api-key": GOOGLE_API_KEY}, json=body)
            return r.json()["candidates"][0]["content"]["parts"][0]["text"]
    except: return "Error analyzing image."

async def ask_gemini_text(question, lang):
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"
    prompt = f"You are a dental clinic receptionist. Answer in {lang}. Keep it short."
    body = {"contents": [{"parts": [{"text": f"{prompt}\nUser: {question}"}]}]}
    headers = {"Content-Type": "application/json", "x-goog-api-key": GOOGLE_API_KEY}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(url, headers=headers, json=body)
            return r.json()["candidates"][0]["content"]["parts"][0]["text"]
    except: return "System busy."

# --- KEYBOARDS ---
def language_keyboard():
    return {"keyboard": [
        [{"text": "فارسی / Farsi"}, {"text": "English"}],
        [{"text": "العربية / Arabic"}, {"text": "Русский / Russian"}]
    ], "resize_keyboard": True, "one_time_keyboard": True}

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
def startup_event(): init_db()

@app.get("/")
async def root(): return {"status": "ok", "message": "Dental Bot V7 (Personalized)"}

@app.get("/trigger-reminders")
async def trigger_reminders():
    reminders = get_pending_reminders()
    count = 0
    for slot_id, dt_str, chat_id, name, lang in reminders:
        texts = TRANS.get(lang, TRANS["en"])
        date_part = dt_str.split(" ")[0]
        time_part = dt_str.split(" ")[1]
        # پیام یادآوری با نام کاربر
        msg = f"⏰ {texts['reminder_msg'].format(name=name, date=date_part, time=time_part)}"
        await send_message(chat_id, msg)
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
            await send_message(chat_id, f"Sent to {len(users)} users.")
        return {"ok": True}

    # Load State
    with sqlite3.connect(DB_NAME) as conn:
        state_row = conn.execute("SELECT flow_type, step, data FROM states WHERE chat_id=?", (chat_id,)).fetchone()
        current_state = {"flow_type": state_row[0], "step": state_row[1], "data": json.loads(state_row[2])} if state_row else None
    
    user_row = get_user(chat_id)
    # user_row[0]=name, [1]=whatsapp, [2]=phone, [3]=lang
    user_name = user_row[0] if user_row else None
    lang = user_row[3] if user_row else "en"
    texts = TRANS.get(lang, TRANS["en"])

    # --- IMAGE HANDLING ---
    if msg.get("photo"):
        if not user_row:
            await send_message(chat_id, "Please register first / لطفاً ثبت‌نام کنید")
            return {"ok": True}
        
        if msg["photo"][-1].get("file_size", 0) > 19 * 1024 * 1024:
            await send_message(chat_id, texts["file_too_large"])
            return {"ok": True}
            
        await send_message(chat_id, texts["photo_analyzing"])
        f_info = await get_file_info(msg["photo"][-1]["file_id"])
        if f_info:
            res = await analyze_image_with_gemini(f_info["file_path"], msg.get("caption", ""), lang)
            # پاسخ هوش مصنوعی با نام کاربر
            prefix = texts["greeting"].format(name=user_name)
            await send_message(chat_id, f"{prefix}\n\n🦷 **AI:**\n{res}{texts['photo_disclaimer']}", reply_markup=main_keyboard(lang))
        return {"ok": True}

    # --- CONTACT VERIFICATION (PHONE STEP) ---
    if current_state and current_state["step"] == "phone":
        if msg.get("contact"):
            contact = msg["contact"]
            if contact.get("user_id") != chat_id:
                await send_message(chat_id, "Error: Not your contact.", reply_markup=contact_keyboard(lang))
                return {"ok": True}
            
            data = current_state["data"]
            # ذخیره نهایی: نام، واتساپ، تلفن تایید شده، زبان
            upsert_user(chat_id, name=data.get("name"), whatsapp=data.get("whatsapp"), phone=contact.get("phone_number"), lang=data.get("lang"))
            
            with sqlite3.connect(DB_NAME) as conn: conn.execute("DELETE FROM states WHERE chat_id=?", (chat_id,)); conn.commit()
            
            welcome_msg = TRANS.get(data["lang"], TRANS["en"])["reg_complete"]
            await send_message(chat_id, welcome_msg, reply_markup=main_keyboard(data["lang"]))
        else:
            # اگر کاربر تایپ کرد (با اینکه باید دکمه می‌زد)
            err = TRANS.get(current_state["data"]["lang"], TRANS["en"])["use_button_error"]
            await send_message(chat_id, err, reply_markup=contact_keyboard(current_state["data"]["lang"]))
        return {"ok": True}

    # --- START ---
    if text == "/start":
        with sqlite3.connect(DB_NAME) as conn:
            conn.execute("DELETE FROM states WHERE chat_id=?", (chat_id,))
            conn.execute("INSERT INTO states (chat_id, flow_type, step, data) VALUES (?,?,?,?)", (chat_id, "reg", "lang", "{}"))
            conn.commit()
        await send_message(chat_id, "Select Language / زبان را انتخاب کنید:", reply_markup=language_keyboard())
        return {"ok": True}

    # --- REGISTRATION FLOW ---
    if current_state and current_state["flow_type"] == "reg":
        step = current_state["step"]
        data = current_state["data"]

        # 1. Language
        if step == "lang":
            sel_lang = None
            t_lower = text.lower()
            if "فارسی" in text: sel_lang = "fa"
            elif "english" in t_lower: sel_lang = "en"
            elif "arabic" in t_lower or "العربية" in text: sel_lang = "ar"
            elif "russian" in t_lower or "русский" in text: sel_lang = "ru"
            
            if not sel_lang:
                await send_message(chat_id, "Please select from buttons.", reply_markup=language_keyboard())
                return {"ok": True}
                
            upsert_user(chat_id, lang=sel_lang)
            with sqlite3.connect(DB_NAME) as conn:
                # برو به مرحله نام
                conn.execute("UPDATE states SET step=?, data=? WHERE chat_id=?", ("name", json.dumps({"lang": sel_lang}), chat_id))
                conn.commit()
            
            await send_message(chat_id, TRANS[sel_lang]["name_prompt"])
            return {"ok": True}

        # 2. Name
        if step == "name":
            data["name"] = text
            with sqlite3.connect(DB_NAME) as conn:
                # برو به مرحله واتساپ
                conn.execute("UPDATE states SET step=?, data=? WHERE chat_id=?", ("whatsapp", json.dumps(data), chat_id))
                conn.commit()
            
            await send_message(chat_id, TRANS[data["lang"]]["whatsapp_prompt"])
            return {"ok": True}

        # 3. WhatsApp
        if step == "whatsapp":
            data["whatsapp"] = text
            with sqlite3.connect(DB_NAME) as conn:
                # برو به مرحله تلفن (تاییدیه)
                conn.execute("UPDATE states SET step=?, data=? WHERE chat_id=?", ("phone", json.dumps(data), chat_id))
                conn.commit()
            
            p_msg = TRANS[data["lang"]]["phone_prompt"]
            await send_message(chat_id, p_msg, reply_markup=contact_keyboard(data["lang"]))
            return {"ok": True}

    # اگر ثبت نام نکرده باشد
    if not user_row:
        await send_message(chat_id, "Type /start to register.")
        return {"ok": True}

    # --- BOOKING FLOW ---
    if current_state and current_state["flow_type"] == "booking":
        step = current_state["step"]
        data = current_state["data"]
        
        if "cancel" in text.lower() or "لغو" in text or "الغاء" in text or "отмена" in text.lower():
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
            clicked_slot = text
            full_slot = None
            all_slots = get_available_slots()
            for s in all_slots:
                if clicked_slot in s: full_slot = s; break
            
            if full_slot and book_slot_atomic(full_slot, chat_id):
                with sqlite3.connect(DB_NAME) as conn: conn.execute("DELETE FROM states WHERE chat_id=?", (chat_id,)); conn.commit()
                await send_message(chat_id, texts["booking_done"], reply_markup=main_keyboard(lang))
                if ADMIN_CHAT_ID:
                    try: await send_message(int(ADMIN_CHAT_ID), f"📅 New Booking:\nName: {user_name}\nWhatsApp: {user_row[1]}\nPhone: {user_row[2]}\nTime: {full_slot}\nSvc: {data.get('service')}")
                    except: pass
            else:
                new_slots = get_available_slots()
                await send_message(chat_id, texts["slot_taken"], reply_markup=slots_keyboard(new_slots))
            return {"ok": True}

    # --- MAIN MENU HANDLER ---
    flat_btns = [b for r in texts["buttons"] for b in r]
    if text in flat_btns:
        idx = flat_btns.index(text)
        prefix = texts["greeting"].format(name=user_name) # شخصی سازی پاسخ

        if idx == 0: # Services
            await send_message(chat_id, f"{prefix}\n{texts['services_reply']}", reply_markup=main_keyboard(lang))
        elif idx == 1: # Hours
            await send_message(chat_id, f"{prefix}\n{texts['hours_reply']}", reply_markup=main_keyboard(lang))
        elif idx == 2: # Book
             with sqlite3.connect(DB_NAME) as conn: conn.execute("INSERT OR REPLACE INTO states (chat_id, flow_type, step, data) VALUES (?,?,?,?)", (chat_id, "booking", "service", "{}")); conn.commit()
             await send_message(chat_id, f"{prefix}{texts['booking_prompt']}")
        elif idx == 3: # Address
             await send_message(chat_id, f"{prefix}\n{texts['address_reply']}", reply_markup=main_keyboard(lang))
        elif idx == 4: # Ask
             hint = {"fa": "سوال خود را بپرسید...", "en": "Ask your question...", "ar": "اكتب سؤالك...", "ru": "Введите вопрос..."}
             await send_message(chat_id, hint.get(lang, "Type..."))
        return {"ok": True}

    # --- AI CHAT (TEXT) ---
    if user_row:
        gemini_ans = await ask_gemini_text(text, lang)
        prefix = texts["greeting"].format(name=user_name)
        await send_message(chat_id, f"{prefix}{gemini_ans}", reply_markup=main_keyboard(lang))

    return {"ok": True}
