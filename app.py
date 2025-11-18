import os
import sqlite3
import json
from fastapi import FastAPI, Request
import httpx
from dotenv import load_dotenv

# بارگذاری متغیرهای محیطی
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

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
if not GOOGLE_API_KEY:
    raise RuntimeError("GOOGLE_API_KEY is not set")

# -----------------------------------------
# DATABASE FUNCTIONS (PERSISTENCE FIX)
# -----------------------------------------
def init_db():
    """ایجاد جداول دیتابیس برای ذخیره اطلاعات کاربران و وضعیت‌ها"""
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                chat_id INTEGER PRIMARY KEY,
                name TEXT,
                phone TEXT,
                lang TEXT DEFAULT 'fa'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS states (
                chat_id INTEGER PRIMARY KEY,
                flow_type TEXT,  -- 'reg' or 'booking'
                step TEXT,
                data TEXT        -- JSON string for temporary data
            )
        """)
        conn.commit()

def get_user(chat_id):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.execute("SELECT name, phone, lang FROM users WHERE chat_id = ?", (chat_id,))
        return cursor.fetchone()

def upsert_user(chat_id, name=None, phone=None, lang=None):
    with sqlite3.connect(DB_NAME) as conn:
        # ابتدا چک می‌کنیم کاربر وجود دارد یا نه
        cursor = conn.execute("SELECT * FROM users WHERE chat_id = ?", (chat_id,))
        if cursor.fetchone():
            # آپدیت فقط فیلدهایی که مقدار دارند
            query = "UPDATE users SET "
            params = []
            updates = []
            if name:
                updates.append("name = ?")
                params.append(name)
            if phone:
                updates.append("phone = ?")
                params.append(phone)
            if lang:
                updates.append("lang = ?")
                params.append(lang)
            
            if updates:
                query += ", ".join(updates) + " WHERE chat_id = ?"
                params.append(chat_id)
                conn.execute(query, params)
        else:
            # ایجاد کاربر جدید
            conn.execute("INSERT INTO users (chat_id, name, phone, lang) VALUES (?, ?, ?, ?)", 
                         (chat_id, name, phone, lang or 'fa'))
        conn.commit()

def set_state(chat_id, flow_type, step, data=None):
    data_json = json.dumps(data) if data else "{}"
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("INSERT OR REPLACE INTO states (chat_id, flow_type, step, data) VALUES (?, ?, ?, ?)",
                     (chat_id, flow_type, step, data_json))
        conn.commit()

def get_state(chat_id):
    with sqlite3.connect(DB_NAME) as conn:
        cursor = conn.execute("SELECT flow_type, step, data FROM states WHERE chat_id = ?", (chat_id,))
        row = cursor.fetchone()
        if row:
            return {"flow_type": row[0], "step": row[1], "data": json.loads(row[2])}
        return None

def clear_state(chat_id):
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("DELETE FROM states WHERE chat_id = ?", (chat_id,))
        conn.commit()

# راه‌اندازی دیتابیس در شروع برنامه
init_db()

# -----------------------------------------
# HELPERS
# -----------------------------------------
async def send_message(chat_id: int, text: str, reply_markup: dict | None = None) -> None:
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            await client.post(f"{TELEGRAM_URL}/sendMessage", json=payload)
    except Exception as e:
        print(f"Error sending message to {chat_id}: {e}")

def language_keyboard() -> dict:
    return {
        "keyboard": [
            [{"text": "فارسی / Farsi"}, {"text": "English"}],
            [{"text": "العربية / Arabic"}, {"text": "Русский / Russian"}],
        ],
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }

def main_keyboard() -> dict:
    return {
        "keyboard": [
            [{"text": "خدمات"}, {"text": "ساعات کاری"}],
            [{"text": "رزرو نوبت"}, {"text": "آدرس مرکز"}],
            [{"text": "سوال از منشی"}],
        ],
        "resize_keyboard": True,
    }

def map_language_choice(text: str) -> str | None:
    t = text.strip().lower()
    if "farsi" in t or "فارسی" in t: return "fa"
    if "english" in t or t == "en": return "en"
    if "arabic" in t or "العربية" in t or "عرب" in t: return "ar"
    if "russian" in t or "рус" in t: return "ru"
    return None

def decorate_with_name(raw_answer: str, name: str | None, lang: str | None) -> str:
    if not name: return raw_answer
    if lang == "fa": return f"{name} عزیز، {raw_answer}"
    if lang == "ar": return f"عزيزي/عزيزتي {name}، {raw_answer}"
    if lang == "ru": return f"{name}, {raw_answer}"
    return f"Dear {name}, {raw_answer}"

async def ask_gemini(question: str, name: str | None = None, lang: str | None = None) -> str:
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    
    system_prompt = """
You are an AI receptionist for "Gemini Medical Center", a dental clinic in Dubai.
Address: 635 Al Wasl Rd - Al Safa 1 - Dubai. Phone: +971 4 225 2000.
Opening hours: every day 10:00–21:00.
Answer in the SAME language as the user.
Do NOT give medical diagnosis. Be short and friendly.
"""
    context_prefix = ""
    if name: context_prefix += f"Patient name: {name}.\n"
    if lang: context_prefix += f"Language: {lang}.\n"
    
    body = {
        "contents": [{"role": "user", "parts": [{"text": system_prompt + "\n\nUser: " + context_prefix + question}]}]
    }
    headers = {"Content-Type": "application/json", "x-goog-api-key": GOOGLE_API_KEY}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(url, headers=headers, json=body)
            r.raise_for_status() # Raise error for 4xx/5xx
            data = r.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        print(f"Gemini Error: {e}") # Log error for debugging
        if lang == "fa": return "متأسفانه الان نمی‌توانم پاسخ دهم. لطفاً بعداً تلاش کنید."
        return "I apologize, I cannot answer right now. Please try again later."

# -----------------------------------------
# ROUTES
# -----------------------------------------
@app.get("/")
async def home():
    return {"status": "ok", "message": "Gemini Dental Bot (SQLite Version) Running"}

@app.post("/webhook")
async def webhook(request: Request):
    try:
        data = await request.json()
    except:
        return {"ok": True} # Handle bad requests gracefully

    message = data.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = (message.get("text") or "").strip()

    if not chat_id or not text:
        return {"ok": True}

    # بازیابی اطلاعات کاربر از دیتابیس
    user_row = get_user(chat_id) # (name, phone, lang)
    user_name = user_row[0] if user_row else None
    user_phone = user_row[1] if user_row else None
    user_lang = user_row[2] if user_row else "fa"
    
    # بازیابی وضعیت فعلی
    current_state = get_state(chat_id)

    # ---------------------------------
    # /start
    # ---------------------------------
    if text == "/start":
        clear_state(chat_id)
        # شروع ثبت نام جدید
        set_state(chat_id, "reg", "lang")
        await send_message(
            chat_id,
            "سلام، من منشی هوشمند Gemini Medical Center هستم.\nلطفاً زبان خود را انتخاب کنید:",
            reply_markup=language_keyboard(),
        )
        return {"ok": True}

    # ---------------------------------
    # Registration Flow
    # ---------------------------------
    if current_state and current_state["flow_type"] == "reg":
        step = current_state["step"]
        state_data = current_state["data"]

        if step == "lang":
            lang_code = map_language_choice(text)
            if not lang_code:
                await send_message(chat_id, "لطفاً یکی از گزینه‌های زبان را انتخاب کنید / Please select a language", reply_markup=language_keyboard())
                return {"ok": True}
            
            # ذخیره زبان در دیتابیس کاربر (به صورت موقت یا دائم)
            upsert_user(chat_id, lang=lang_code)
            set_state(chat_id, "reg", "name", {"lang": lang_code})
            
            msgs = {
                "fa": "لطفاً نام خود را وارد کنید:",
                "en": "Please enter your name:",
                "ar": "من فضلك اكتب اسمك:",
                "ru": "Пожалуйста, введите ваше имя:"
            }
            await send_message(chat_id, msgs.get(lang_code, msgs["en"]))
            return {"ok": True}

        if step == "name":
            state_data["name"] = text
            set_state(chat_id, "reg", "phone", state_data)
            lang = state_data.get("lang", "fa")
            msgs = {
                "fa": "شماره تماس خود را وارد کنید:",
                "en": "Please enter your phone number:",
                "ar": "اكتب رقم هاتفك:",
                "ru": "Введите номер телефона:"
            }
            await send_message(chat_id, msgs.get(lang, msgs["en"]))
            return {"ok": True}

        if step == "phone":
            # Simple validation can be added here
            upsert_user(chat_id, name=state_data.get("name"), phone=text, lang=state_data.get("lang"))
            clear_state(chat_id)
            
            lang = state_data.get("lang", "fa")
            name = state_data.get("name")
            
            msgs = {
                "fa": f"{name} عزیز، ثبت‌نام انجام شد.",
                "en": f"Dear {name}, registration complete.",
                "ar": f"{name}، تم التسجيل.",
                "ru": f"{name}, регистрация завершена."
            }
            await send_message(chat_id, msgs.get(lang, msgs["en"]), reply_markup=main_keyboard())
            return {"ok": True}

    # ---------------------------------
    # Check Authentication
    # ---------------------------------
    if not user_row:
        # اگر کاربر در دیتابیس نیست، مجبور به ثبت نام است
        set_state(chat_id, "reg", "lang")
        await send_message(chat_id, "لطفاً ابتدا زبان را انتخاب کنید:", reply_markup=language_keyboard())
        return {"ok": True}

    # ---------------------------------
    # Booking Flow
    # ---------------------------------
    if current_state and current_state["flow_type"] == "booking" and not text.startswith("/"):
        step = current_state["step"]
        state_data = current_state["data"]

        if text.lower() in ["لغو", "cancel", "الغاء", "отмена"]:
            clear_state(chat_id)
            await send_message(chat_id, decorate_with_name("لغو شد.", user_name, user_lang), reply_markup=main_keyboard())
            return {"ok": True}

        if step == "service":
            state_data["service"] = text
            set_state(chat_id, "booking", "doctor", state_data)
            msg = {
                "fa": "نام دکتر مدنظر را بفرمایید (یا بنویسید 'فرقی نمی‌کند'):",
                "en": "Preferred doctor name (or type 'Any'):",
                "ar": "اسم الطبيب المفضل (أو اكتب 'أي طبيب'):",
                "ru": "Имя врача (или 'Любой'):"
            }
            await send_message(chat_id, msg.get(user_lang, msg["en"]))
            return {"ok": True}

        if step == "doctor":
            state_data["doctor"] = text
            set_state(chat_id, "booking", "datetime", state_data)
            msg = {
                "fa": "تاریخ و ساعت مناسب را بفرمایید:",
                "en": "Preferred date and time:",
                "ar": "التاريخ والوقت المفضل:",
                "ru": "Желаемая дата и время:"
            }
            await send_message(chat_id, msg.get(user_lang, msg["en"]))
            return {"ok": True}

        if step == "datetime":
            summary = (
                f"New Booking:\nName: {user_name}\nPhone: {user_phone}\n"
                f"Service: {state_data.get('service')}\nDoctor: {state_data.get('doctor')}\n"
                f"Time: {text}\nChatID: {chat_id}"
            )
            
            # ارسال به ادمین (با هندلینگ خطا)
            if ADMIN_CHAT_ID:
                try:
                    await send_message(int(ADMIN_CHAT_ID), summary)
                except Exception as e:
                    print(f"Failed to notify admin: {e}")

            clear_state(chat_id)
            
            msg = {
                "fa": "درخواست شما ثبت شد. همکاران ما تماس می‌گیرند.",
                "en": "Booking received. We will contact you shortly.",
                "ar": "تم استلام طلب الحجز. سنتصل بك قريباً.",
                "ru": "Бронирование получено. Мы скоро свяжемся с вами."
            }
            await send_message(chat_id, msg.get(user_lang, msg["en"]), reply_markup=main_keyboard())
            return {"ok": True}

    # ---------------------------------
    # Main Menu Handling
    # ---------------------------------
    if text == "خدمات":
        # متن‌ها را می‌توان بر اساس user_lang ترجمه کرد، فعلا برای سادگی فارسی/انگلیسی
        msg = "Dental Services: Implants, Orthodontics, Veneers, Cleaning..." if user_lang == "en" else "خدمات: ایمپلنت، ارتودنسی، لمینت، جرمگیری..."
        await send_message(chat_id, decorate_with_name(msg, user_name, user_lang), reply_markup=main_keyboard())
        return {"ok": True}

    if text == "ساعات کاری":
        msg = "10:00 AM - 09:00 PM"
        await send_message(chat_id, msg, reply_markup=main_keyboard())
        return {"ok": True}
    
    if text == "آدرس مرکز":
        msg = "635 Al Wasl Rd - Dubai"
        await send_message(chat_id, msg, reply_markup=main_keyboard())
        return {"ok": True}

    if text == "رزرو نوبت":
        set_state(chat_id, "booking", "service", {})
        msg = {
            "fa": "برای چه خدمتی نوبت می‌خواهید؟",
            "en": "Which service do you need?",
            "ar": "ما هي الخدمة المطلوبة؟",
            "ru": "Какая услуга вам нужна?"
        }
        await send_message(chat_id, msg.get(user_lang, msg["en"]), reply_markup=main_keyboard())
        return {"ok": True}

    # ---------------------------------
    # AI Chat
    # ---------------------------------
    # اینجا از user_lang و user_name ذخیره شده در دیتابیس استفاده می‌شود
    raw_answer = await ask_gemini(text, user_name, user_lang)
    await send_message(chat_id, decorate_with_name(raw_answer, user_name, user_lang), reply_markup=main_keyboard())
    return {"ok": True}
