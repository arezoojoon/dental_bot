from fastapi import FastAPI, Request
import httpx
import os

from google import genai
from google.genai import types
from google.genai import client as genai_client

app = FastAPI()

# -----------------------------------------
# CONFIG
# -----------------------------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
if not GOOGLE_API_KEY:
    raise RuntimeError("GOOGLE_API_KEY is not set")

# Optional: admin notifications
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")  # e.g. "123456789"

# In-memory states (for demo only)
USER_PROFILE: dict[int, dict] = {}   # chat_id -> {name, phone, lang}
REG_STATE: dict[int, dict] = {}      # chat_id -> {step, lang?, name?, phone?}
BOOKING_STATE: dict[int, dict] = {}  # chat_id -> {step, service?, doctor?, datetime?}


# -----------------------------------------
# HELPERS
# -----------------------------------------
async def send_message(chat_id: int, text: str, reply_markup: dict | None = None) -> None:
    payload: dict = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    async with httpx.AsyncClient(timeout=20) as client:
        await client.post(f"{TELEGRAM_URL}/sendMessage", json=payload)


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
    if "farsi" in t or "فارسی" in t:
        return "fa"
    if "english" in t or t == "en":
        return "en"
    if "arabic" in t or "العربية" in t or "عرب" in t:
        return "ar"
    if "russian" in t or "рус" in t:
        return "ru"
    return None


def get_profile(chat_id: int) -> dict:
    return USER_PROFILE.get(chat_id, {})


def decorate_with_name(raw_answer: str, name: str | None, lang: str | None) -> str:
    if not name:
        return raw_answer

    if lang == "fa":
        return f"{name} عزیز، {raw_answer}"
    if lang == "ar":
        return f"عزيزي/عزيزتي {name}، {raw_answer}"
    if lang == "ru":
        return f"{name}, {raw_answer}"
    return f"Dear {name}, {raw_answer}"


async def ask_gemini(question: str, name: str | None = None, lang: str | None = None) -> str:
    """Call Google Gemini model via Google AI API."""
    url = (
        "https://generativelanguage.googleapis.com/v1/"
        "models/gemini-1.5-flash:generateContent"
    )

    system_prompt = """
You are an AI receptionist for "Gemini Medical Center", a dental clinic in Dubai.
Address: 635 Al Wasl Rd - Al Safa 1 - Dubai - United Arab Emirates.
Phone: +971 4 225 2000.
Opening hours: every day 10:00–21:00.

You answer in the SAME language as the user.
Main languages are Arabic, English, Persian (Farsi) and Russian, but you can answer in any language the user uses.

You can explain clinic services: checkup, cleaning, whitening, fillings, veneers, implants,
orthodontics, emergency visits, etc.

You MUST NOT give medical diagnosis or treatment plans.
If user asks for diagnosis, clearly say that final diagnosis needs a dentist visit in the clinic.
Be short, friendly and professional like a real receptionist.
"""

    context_prefix = ""
    if name:
        context_prefix += f"Patient name: {name}.\n"
    if lang:
        context_prefix += f"Preferred language code: {lang}.\n"

    full_user_message = context_prefix + question

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": system_prompt},
                    {"text": full_user_message},
                ],
            }
        ]
    }

    params = {"key": GOOGLE_API_KEY}
    headers = {"Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, params=params, json=payload, headers=headers)
        data = r.json()

    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception:
        return "متأسفانه الان نمی‌توانم پاسخ دقیقی بدهم. لطفاً چند لحظه بعد دوباره تلاش کنید."


# -----------------------------------------
# ROUTES
# -----------------------------------------
@app.get("/")
async def home():
    return {"status": "ok", "message": "Gemini Dental Bot with registration running"}


@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    message = data.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = (message.get("text") or "").strip()

    if not chat_id or not text:
        return {"ok": True}

    # ---------------------------------
    # /start: reset profile and start registration
    # ---------------------------------
    if text == "/start":
        USER_PROFILE.pop(chat_id, None)
        REG_STATE[chat_id] = {"step": "lang"}
        BOOKING_STATE.pop(chat_id, None)

        await send_message(
            chat_id,
            "سلام، من منشی هوشمند Gemini Medical Center هستم.\n"
            "برای شروع، لطفاً زبان خود را انتخاب کنید:",
            reply_markup=language_keyboard(),
        )
        return {"ok": True}

    # ---------------------------------
    # Registration flow (language / name / phone)
    # ---------------------------------
    if chat_id in REG_STATE:
        state = REG_STATE[chat_id]
        step = state["step"]

        # Language selection
        if step == "lang":
            lang_code = map_language_choice(text)
            if not lang_code:
                await send_message(
                    chat_id,
                    "لطفاً یکی از گزینه‌های زبان را انتخاب کنید:\n"
                    "فارسی / English / العربية / Русский",
                    reply_markup=language_keyboard(),
                )
                return {"ok": True}

            state["lang"] = lang_code
            state["step"] = "name"

            if lang_code == "fa":
                msg = "لطفاً نام خود را وارد کنید:"
            elif lang_code == "ar":
                msg = "من فضلك اكتب اسمك:"
            elif lang_code == "ru":
                msg = "Пожалуйста, введите ваше имя:"
            else:
                msg = "Please enter your name:"

            await send_message(chat_id, msg)
            return {"ok": True}

        # Name
        if step == "name":
            state["name"] = text
            state["step"] = "phone"

            lang_code = state.get("lang", "fa")
            if lang_code == "fa":
                msg = "شماره واتساپ یا موبایل خود را وارد کنید:"
            elif lang_code == "ar":
                msg = "من فضلك اكتب رقم الواتساب أو الهاتف:"
            elif lang_code == "ru":
                msg = "Пожалуйста, введите номер WhatsApp или телефона:"
            else:
                msg = "Please enter your WhatsApp or mobile number:"

            await send_message(chat_id, msg)
            return {"ok": True}

        # Phone
        if step == "phone":
            state["phone"] = text

            USER_PROFILE[chat_id] = {
                "name": state.get("name"),
                "phone": state.get("phone"),
                "lang": state.get("lang", "fa"),
            }
            lang_code = state.get("lang", "fa")
            name = state.get("name")

            REG_STATE.pop(chat_id, None)

            if lang_code == "fa":
                welcome = (
                    f"{name} عزیز، ثبت‌نام شما انجام شد.\n"
                    "از منوی زیر می‌توانید خدمات، ساعات کاری یا رزرو نوبت را انتخاب کنید."
                )
            elif lang_code == "ar":
                welcome = (
                    f"{name}، تم تسجيل بياناتك.\n"
                    "يمكنك الآن استخدام الأزرار لمعرفة الخدمات أو حجز موعد."
                )
            elif lang_code == "ru":
                welcome = (
                    f"{name}, регистрация завершена.\n"
                    "Теперь вы можете использовать кнопки для просмотра услуг или записи на приём."
                )
            else:
                welcome = (
                    f"Dear {name}, your registration is completed.\n"
                    "You can now use the menu to see services, working hours or book an appointment."
                )

            await send_message(chat_id, welcome, reply_markup=main_keyboard())
            return {"ok": True}

    # ---------------------------------
    # If no profile yet → force registration
    # ---------------------------------
    if chat_id not in USER_PROFILE:
        REG_STATE[chat_id] = {"step": "lang"}
        await send_message(
            chat_id,
            "برای استفاده از منشی هوشمند، ابتدا باید ثبت‌نام کوتاه انجام دهید.\n"
            "لطفاً زبان خود را انتخاب کنید:",
            reply_markup=language_keyboard(),
        )
        return {"ok": True}

    # From here on, profile is available
    profile = get_profile(chat_id)
    user_name = profile.get("name")
    user_lang = profile.get("lang", "fa")

    # ---------------------------------
    # Booking flow
    # ---------------------------------
    if chat_id in BOOKING_STATE and not text.startswith("/"):
        state = BOOKING_STATE[chat_id]
        step = state["step"]

        # Cancel booking
        if text.lower() in ["لغو", "انصراف", "cancel"]:
            del BOOKING_STATE[chat_id]
            await send_message(
                chat_id,
                decorate_with_name(
                    "فرآیند رزرو لغو شد. هر زمان خواستید دوباره از دکمه «رزرو نوبت» استفاده کنید.",
                    user_name,
                    user_lang,
                ),
                reply_markup=main_keyboard(),
            )
            return {"ok": True}

        # Which service
        if step == "service":
            state["service"] = text
            state["step"] = "doctor"
            await send_message(
                chat_id,
                decorate_with_name(
                    "آیا دکتر خاصی مدنظر دارید؟\n"
                    "لطفاً نام دکتر را بنویسید یا اگر فرقی نمی‌کند، بنویسید «فرقی نمی‌کند».",
                    user_name,
                    user_lang,
                ),
                reply_markup=main_keyboard(),
            )
            return {"ok": True}

        # Which doctor
        if step == "doctor":
            state["doctor"] = text
            state["step"] = "datetime"
            await send_message(
                chat_id,
                decorate_with_name(
                    "تاریخ و ساعت ترجیحی را بفرمایید (مثلاً: دوشنبه ساعت ۶ عصر):",
                    user_name,
                    user_lang,
                ),
                reply_markup=main_keyboard(),
            )
            return {"ok": True}

        # Preferred date/time
        if step == "datetime":
            state["datetime"] = text

            summary = (
                "درخواست نوبت جدید برای Gemini Medical Center:\n\n"
                f"نام: {profile.get('name')}\n"
                f"شماره تماس: {profile.get('phone')}\n"
                f"خدمت درخواستی: {state.get('service')}\n"
                f"دکتر مدنظر: {state.get('doctor')}\n"
                f"زمان پیشنهادی: {state.get('datetime')}\n"
                f"Telegram chat id: {chat_id}"
            )

            await send_message(
                chat_id,
                decorate_with_name(
                    summary
                    + "\n\nدرخواست شما ثبت شد. منشی مرکز برای تأیید نهایی با شما تماس خواهد گرفت.",
                    user_name,
                    user_lang,
                ),
                reply_markup=main_keyboard(),
            )

            if ADMIN_CHAT_ID:
                try:
                    await send_message(int(ADMIN_CHAT_ID), summary)
                except Exception:
                    pass

            del BOOKING_STATE[chat_id]
            return {"ok": True}

    # ---------------------------------
    # Main menu buttons
    # ---------------------------------
    if text == "خدمات":
        msg = (
            "خدمات اصلی Gemini Medical Center:\n"
            "• ویزیت و چکاپ دندان\n"
            "• جرمگیری و پولیش\n"
            "• سفید کردن دندان (Whitening)\n"
            "• پرکردن و ترمیم دندان\n"
            "• روکش و لمینت\n"
            "• ایمپلنت\n"
            "• ارتودنسی\n"
            "• درمان‌های اورژانسی\n\n"
            "اگر درباره هر مورد سوال دارید، بپرسید."
        )
        await send_message(
            chat_id,
            decorate_with_name(msg, user_name, user_lang),
            reply_markup=main_keyboard(),
        )
        return {"ok": True}

    if text == "ساعات کاری":
        msg = (
            "ساعات کاری Gemini Medical Center:\n"
            "هر روز از ساعت ۱۰:۰۰ تا ۲۱:۰۰\n\n"
            "برای رزرو نوبت می‌توانید از دکمه «رزرو نوبت» استفاده کنید."
        )
        await send_message(
            chat_id,
            decorate_with_name(msg, user_name, user_lang),
            reply_markup=main_keyboard(),
        )
        return {"ok": True}

    if text == "آدرس مرکز":
        msg = (
            "آدرس Gemini Medical Center:\n"
            "635 Al Wasl Rd - Al Safa 1 - Dubai - United Arab Emirates\n\n"
            "برای مسیریابی، این لینک را در گوگل‌مپ باز کنید:\n"
            "https://maps.google.com/?q=Gemini+Medical+Center+Dubai"
        )
        await send_message(
            chat_id,
            decorate_with_name(msg, user_name, user_lang),
            reply_markup=main_keyboard(),
        )
        return {"ok": True}

    if text == "رزرو نوبت":
        BOOKING_STATE[chat_id] = {"step": "service"}
        await send_message(
            chat_id,
            decorate_with_name(
                "برای چه خدمتی از دندان‌پزشکان Gemini Medical Center نوبت می‌خواهید؟ "
                "(مثلاً: جرمگیری، چکاپ، ایمپلنت و ...)",
                user_name,
                user_lang,
            ),
            reply_markup=main_keyboard(),
        )
        return {"ok": True}

    if text == "سوال از منشی":
        msg = (
            "سوال خود را درباره خدمات، قیمت‌ها یا نحوه رزرو بنویسید.\n"
            "منشی هوشمند براساس اطلاعات کلینیک پاسخ می‌دهد."
        )
        await send_message(
            chat_id,
            decorate_with_name(msg, user_name, user_lang),
            reply_markup=main_keyboard(),
        )
        return {"ok": True}

    # ---------------------------------
    # All other messages → Gemini
    # ---------------------------------
    raw_answer = await ask_gemini(text, user_name, user_lang)
    final_answer = decorate_with_name(raw_answer, user_name, user_lang)

    await send_message(chat_id, final_answer, reply_markup=main_keyboard())
    return {"ok": True}
