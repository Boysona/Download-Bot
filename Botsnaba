import telebot
import logging
import os
from datetime import datetime
from flask import Flask, request

BOT_TOKEN = "7770743573:AAGFMGNZa-WzsOkjYjbN3vgznQEEsR_m0Z4"
WEBHOOK_URL_BASE = "https://download-bot-5sv5.onrender.com"
WEBHOOK_URL_PATH = f"/{BOT_TOKEN}"
WEBHOOK_URL = WEBHOOK_URL_BASE + WEBHOOK_URL_PATH
PORT = int(os.environ.get("PORT", 8443))

bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)
bot_start_time = None

app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

def set_bot_info_and_startup():
    global bot_start_time
    bot_start_time = datetime.now()
    try:
        bot.set_my_description(
            "This bot can transcribe and summarize any media file (voice messages, audio files, or videos) for free\n\n🔥Enjoy unlimited of free usage to start!👌🏻"
        )
        bot.set_my_short_description(
            "This bot can Transcribe and Summarize any media files them in seconds! for free"
        )

        bot.set_my_description(
            "This bot can transcribe and summarize any media file (voice messages, audio files, or videos) for free\n\n🔥Enjoy unlimited of free usage to start!👌🏻",
            language_code="en"
        )
        bot.set_my_short_description(
            "This bot can Transcribe and Summarize any media files them in seconds! for free",
            language_code="en"
        )

        bot.set_my_description(
            "🤖 هذا البوت يحول الرسائل الصوتية والملفات الصوتية والفيديو إلى نصوص ويلخصها مجانًا.\n\n🔥 استمتع باستخدام مجاني غير محدود!",
            language_code="ar"
        )
        bot.set_my_short_description(
            "🎤 بوت لتحويل الصوتيات والفيديو إلى نصوص وتلخيصها مجانًا.",
            language_code="ar"
        )

        bot.set_my_description(
            "🤖 Este bot transcribe mensajes de voz, archivos de audio y videos a texto y los resume gratis.\n\n🔥 ¡Disfruta de uso ilimitado y gratuito!",
            language_code="es"
        )
        bot.set_my_short_description(
            "🎤 Bot para transcribir y resumir audio y video gratis.",
            language_code="es"
        )

        bot.set_my_description(
            "Этот бот может транскрибировать и резюмировать любые медиафайлы (голосовые сообщения, аудиофайлы или видео) бесплатно\n\n🔥Наслаждайтесь неограниченным бесплатным использованием!",
            language_code="ru"
        )
        bot.set_my_short_description(
            "Бот транскрибирует и резюмирует медиафайлы за секунды — бесплатно",
            language_code="ru"
        )

        bot.set_my_description(
            "यह बोट किसी भी मीडिया फाइल (वॉइस संदेश, ऑडियो फाइलें, या वीडियो) को मुफ्त में ट्रांसक्राइब और समरी कर सकता है\n\n🔥शुरू करने के लिए असीमित मुफ्त उपयोग का आनंद लें!👌🏻",
            language_code="hi"
        )
        bot.set_my_short_description(
            "यह बॉट किसी भी मीडिया फ़ाइल को सेकंड में ट्रांसक्राइब और समरी कर सकता है — मुफ्त",
            language_code="hi"
        )

        bot.set_my_description(
            "این بات می‌تواند هر فایل رسانه‌ای (پیام‌های صوتی، فایل‌های صوتی یا ویدیوها) را به‌صورت رایگان رونویسی و خلاصه کند\n\n🔥از استفاده نامحدود رایگان لذت ببرید!👌🏻",
            language_code="fa"
        )
        bot.set_my_short_description(
            "این بات فایل‌های رسانه‌ای را در چند ثانیه رونویسی و خلاصه می‌کند — رایگان",
            language_code="fa"
        )

        bot.delete_my_commands()
        logging.info("Bot info updated with descriptions and short descriptions for multiple languages.")
    except Exception as e:
        logging.error(f"Failed to set bot info: {e}")

@bot.message_handler(content_types=["text"])
def default_handler(message):
    bot.reply_to(
        message,
        "👋 Send me any text and I will convert it into speech using Microsoft Edge TTS."
    )

@bot.message_handler(content_types=["voice", "audio", "video"])
def media_handler(message):
    bot.reply_to(message, "⏳ Processing your media...")
    text = fake_tts()
    bot.send_message(message.chat.id, text)

def fake_tts():
    return "🔊 (Here is where the generated speech/audio will be returned — add TTS engine later)."

@app.route(WEBHOOK_URL_PATH, methods=["POST"])
def webhook():
    if request.headers.get("content-type") == "application/json":
        json_str = request.get_data().decode("utf-8")
        update = telebot.types.Update.de_json(json_str)
        bot.process_new_updates([update])
        return "", 200
    else:
        return "Bad Request", 403

if __name__ == "__main__":
    set_bot_info_and_startup()
    try:
        bot.remove_webhook()
        logging.info("Webhook removed successfully.")
    except Exception as e:
        logging.error(f"Failed to remove webhook: {e}")

    try:
        bot.set_webhook(url=WEBHOOK_URL)
        logging.info(f"Webhook set successfully to URL: {WEBHOOK_URL}")
    except Exception as e:
        logging.error(f"Failed to set webhook: {e}")

    app.run(host="0.0.0.0", port=PORT)
