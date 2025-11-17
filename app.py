import os
from fastapi import FastAPI, Request
import httpx

app = FastAPI()

# -----------------------------------------
# CONFIG
# -----------------------------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
if not OPENAI_KEY:
    raise RuntimeError("OPENAI_API_KEY is not set")

# Optional: send bookings also to an admin chat (your own Telegram ID)
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")  # e.g. "123456789"

# In-memory booking state: chat_id -> dict
BOOKING_STATE: dict[int, dict] = {}


# -----------------------------------------
# HELPERS
# -----------------------------------------
async def send_message(chat_id, text, reply_markup=None):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    async with httpx.AsyncClient(timeout=15) as client:
        await client.post(f"{TELEGRAM_URL}/sendMessage", json=payload)


def main_keyboard():
    return {
        "keyboard": [
            [{"text": "خدمات"}, {"text": "ساعات کاری"}],
            [{"text": "رزرو نوبت"}, {"text": "آدرس مرکز"}],
            [{"text": "سوال از منشی"}],
        ],
        "resize_keyboard": True,
    }


async def ask_gpt(question: str) -> str:
    """Call OpenAI ChatGPT as Gemini receptionist."""
    url = "https://api.openai.com/v1/chat/completions"

    system_prompt = """
You are an AI receptionist for "Gemini Medical Center", a dental clinic in Dubai.
Address: 635 Al Wasl Rd - Al Safa 1 - Dubai - United Arab Emirates.
Phone: +971 4 225 2000.
Opening hours: every day 10:00–21:00.

You answer in the SAME language as the user (Arabic, English, or Persian).
You can explain clinic services: checkup, cleaning, whitening, fillings, veneers, implants,
orthodontics, emergency visits, etc.

You MUST NOT give medical diagnosis or treatment plans.
If user asks for diagnosis, clearly say that final diagnosis needs a dentist visit in the clinic.
Be short, friendly and professional like a real receptionist.
    """

    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ],
    }

    headers = {
        "Authorization": f"Bearer {OPENAI_KEY}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, json=payload, headers=headers)
        data = r.json()
        return data["choices"][0]["message"]["content"]


# -----------------------------------------
# ROUTES
# -----------------------------------------
@app.get("/")
async def home():
    return {"status": "ok", "message": "Gemini Dental Bot running"}


@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    message = data.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = (message.get("text") or "").strip()

    if not chat_id or not text:
        return {"ok": True}

    # --------------------
    # Handle booking state
    # --------------------
    if chat_id in BOOKING_STATE and not text.startswith("/"):
        state = BOOKING_STATE[chat_id]
        step = state["step"]

        # allow cancel
        if text.lower() in ["لغو", "انصراف", "cancel"]:
            del BOOKING_STATE[chat_id]
            await send_message(
                chat_id,
                "فرآیند رزرو لغو شد. هر زمان خواستید دوباره از دکمه «رزرو نوبت» استفاده کنید.",
                reply_markup=main_keyboard(),
            )
            return {"ok": True}

        if step == "name":
            state["name"] = text
            state["step"] = "phone"
            await send_message(
                chat_id,
                "شماره واتساپ یا موبایل را بفرمایید:",
                reply_markup=main_keyboard(),
            )
            return {"ok": True}

        if step == "phone":
            state["phone"] = text
            state["step"] = "service"
            await send_message(
                chat_id,
                "برای چه خدمتی نوبت می‌خواهید؟ (مثلاً: جرمگیری، چکاپ، ایمپلنت و ...)",
                reply_markup=main_keyboard(),
            )
            return {"ok": True}

        if step == "service":
            state["service"] = text
            state["step"] = "datetime"
            await send_message(
                chat_id,
                "تاریخ و ساعت ترجیحی را بفرمایید (مثلاً: دوشنبه ساعت ۶ عصر):",
                reply_markup=main_keyboard(),
            )
            return {"ok": True}

        if step == "datetime":
            state["datetime"] = text

            # Build summary
            summary = (
                "درخواست نوبت جدید برای Gemini Medical Center:\n\n"
                f"نام: {state.get('name')}\n"
                f"شماره تماس: {state.get('phone')}\n"
                f"خدمت درخواستی: {state.get('service')}\n"
                f"زمان پیشنهادی: {state.get('datetime')}\n"
                f"Telegram chat id: {chat_id}"
            )

            # Send confirmation to patient
            await send_message(
                chat_id,
                summary
                + "\n\nدرخواست شما ثبت شد. منشی مرکز برای تأیید نهایی با شما تماس خواهد گرفت.",
                reply_markup=main_keyboard(),
            )

            # Optionally send to admin chat
            if ADMIN_CHAT_ID:
                try:
                    await send_message(int(ADMIN_CHAT_ID), summary)
                except Exception:
                    # ignore admin send error in demo
                    pass

            del BOOKING_STATE[chat_id]
            return {"ok": True}

    # --------------------
    # Commands & buttons
    # --------------------
    if text == "/start":
        BOOKING_STATE.pop(chat_id, None)
        await send_message(
            chat_id,
            "سلام، من منشی هوشمند Gemini Medical Center هستم.\n"
            "می‌تونم درباره خدمات، ساعات کاری و رزرو نوبت راهنمایی‌تان کنم.",
            reply_markup=main_keyboard(),
        )
        return {"ok": True}

    if text == "خدمات":
        await send_message(
            chat_id,
            "خدمات اصلی Gemini Medical Center:\n"
            "• ویزیت و چکاپ دندان\n"
            "• جرمگیری و پولیش\n"
            "• سفید کردن دندان (Whitening)\n"
            "• پرکردن و ترمیم دندان\n"
            "• روکش و لمینت\n"
            "• ایمپلنت\n"
            "• ارتودنسی\n"
            "• درمان‌های اورژانسی\n\n"
            "اگر درباره هر مورد سوال دارید، بپرسید.",
            reply_markup=main_keyboard(),
        )
        return {"ok": True}

    if text == "ساعات کاری":
        await send_message(
            chat_id,
            "ساعات کاری Gemini Medical Center:\n"
            "هر روز از ساعت ۱۰:۰۰ تا ۲۱:۰۰\n\n"
            "برای رزرو نوبت می‌توانید از دکمه «رزرو نوبت» استفاده کنید.",
            reply_markup=main_keyboard(),
        )
        return {"ok": True}

    if text == "آدرس مرکز":
        await send_message(
            chat_id,
            "آدرس Gemini Medical Center:\n"
            "635 Al Wasl Rd - Al Safa 1 - Dubai - United Arab Emirates\n\n"
            "برای مسیریابی، این لینک را در گوگل‌مپ باز کنید:\n"
            "https://maps.google.com/?q=Gemini+Medical+Center+Dubai",
            reply_markup=main_keyboard(),
        )
        return {"ok": True}

    if text == "رزرو نوبت":
        BOOKING_STATE[chat_id] = {"step": "name"}
        await send_message(
            chat_id,
            "برای رزرو نوبت جدید لطفاً ابتدا نام خود را بفرمایید:\n"
            "(در هر مرحله اگر خواستید لغو کنید، بنویسید «لغو»)",
            reply_markup=main_keyboard(),
        )
        return {"ok": True}

    if text == "سوال از منشی":
        await send_message(
            chat_id,
            "سوال خود را درباره خدمات، قیمت‌ها یا نحوه رزرو بنویسید.\n"
            "منشی هوشمند براساس اطلاعات کلینیک پاسخ می‌دهد.",
            reply_markup=main_keyboard(),
        )
        return {"ok": True}

    # --------------------
    # Everything else → ChatGPT
    # --------------------
    answer = await ask_gpt(text)
    await send_message(chat_id, answer, reply_markup=main_keyboard())
    return {"ok": True}

