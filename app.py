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


# -----------------------------------------
# HELPERS
# -----------------------------------------
async def send_message(chat_id, text, reply_markup=None):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    async with httpx.AsyncClient() as client:
        await client.post(f"{TELEGRAM_URL}/sendMessage", json=payload)


def main_keyboard():
    return {
        "keyboard": [
            [{"text": "خدمات"}, {"text": "ساعات کاری"}],
            [{"text": "رزرو نوبت"}, {"text": "آدرس مطب"}],
            [{"text": "سوال از منشی"}]
        ],
        "resize_keyboard": True
    }


async def ask_gpt(question):
    """Ask ChatGPT API"""
    url = "https://api.openai.com/v1/chat/completions"

    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": 
             "You are a helpful dental clinic assistant. Answer politely. Do NOT give medical advice. For medical advice say: 'تشخیص قطعی فقط بعد از ویزیت پزشک ممکن است.'"},
            {"role": "user", "content": question}
        ]
    }

    headers = {
        "Authorization": f"Bearer {OPENAI_KEY}",
        "Content-Type": "application/json"
    }

    async with httpx.AsyncClient() as client:
        r = await client.post(url, json=payload, headers=headers)
        data = r.json()
        return data["choices"][0]["message"]["content"]


# -----------------------------------------
# ROUTES
# -----------------------------------------
@app.get("/")
async def home():
    return {"status": "ok", "message": "Dental Bot is running"}


@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()

    msg = data.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    text = msg.get("text", "").strip()

    # No text? Ignore
    if not text:
        return {"ok": True}

    # ----------------------
    # START
    # ----------------------
    if text == "/start":
        await send_message(
            chat_id,
            "سلام، من منشی هوشمند مطب دندان‌پزشکی هستم.\n"
            "چطور می‌تونم کمکتون کنم؟",
            reply_markup=main_keyboard()
        )
        return {"ok": True}

    # ----------------------
    # BUTTONS
    # ----------------------
    if text == "خدمات":
        await send_message(
            chat_id,
            "لیست خدمات مطب:\n"
            "• جرمگیری\n"
            "• کامپوزیت\n"
            "• لمینت\n"
            "• ایمپلنت\n"
            "• ارتودنسی\n"
            "• ویزیت دکتر\n"
            "اگر درباره هرکدام سوال داری، بپرس.",
            reply_markup=main_keyboard()
        )
        return {"ok": True}

    if text == "ساعات کاری":
        await send_message(
            chat_id,
            "ساعات کاری مطب:\n"
            "شنبه تا پنج‌شنبه: ۱۰ صبح تا ۸ شب\n"
            "جمعه تعطیل است.",
            reply_markup=main_keyboard()
        )
        return {"ok": True}

    if text == "آدرس مطب":
        await send_message(
            chat_id,
            "آدرس:\nدبی – منطقه القوز – ساختمان فلان\n\n"
            "لوکیشن روی نقشه:\nhttps://maps.google.com/",
            reply_markup=main_keyboard()
        )
        return {"ok": True}

    if text == "رزرو نوبت":
        await send_message(
            chat_id,
            "برای رزرو نوبت لطفاً یک پیام شامل موارد زیر بفرستید:\n"
            "نام\n"
            "شماره واتساپ\n"
            "نوع خدمت\n"
            "تاریخ دلخواه\n\n"
            "پس از ارسال، منشی هوشمند پیام را ثبت می‌کند.",
            reply_markup=main_keyboard()
        )
        return {"ok": True}

    if text == "سوال از منشی":
        await send_message(
            chat_id,
            "سوال خود را بنویسید. منشی هوشمند پاسخ می‌دهد.",
            reply_markup=main_keyboard()
        )
        return {"ok": True}

    # ----------------------
    # All other messages → ChatGPT
    # ----------------------
    answer = await ask_gpt(text)
    await send_message(chat_id, answer, reply_markup=main_keyboard())

    return {"ok": True}

