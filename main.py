# main.py
import os
import subprocess
import logging
import time
import requests
import json
from moviepy.video.io.VideoFileClip import VideoFileClip
import edge_tts
import asyncio

import telebot
from telebot import types

from flask import Flask, request

# ---------------------------
# ========== CONFIG =========
# ---------------------------

# ---- Tokens / Keys (directly embedded as requested) ----
TELEGRAM_TOKEN = "8371007825:AAFpp_SVygKKTR6y0PlX9W4q9LBrgwLA6b8"
ASSEMBLYAI_KEY = "a356bbda79da4fd8a77a12ad819c47e2"
GEMINI_KEY = "AIzaSyDLxRqMWmjpLW0IRh85JwLdLcYMEWY0_kk"

# Webhook URL (as provided)
WEBHOOK_URL = "https://download-bot-5sv5.onrender.com"

# ---------------------------
# ========== FFMPEG =========
# Auto-detect ffmpeg binary
# ---------------------------
FFMPEG_ENV = os.environ.get("FFMPEG_BINARY", "")
POSSIBLE_FFMPEG_PATHS = [FFMPEG_ENV, "./ffmpeg", "/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg", "ffmpeg"]
FFMPEG_BINARY = None
for p in POSSIBLE_FFMPEG_PATHS:
    if not p:
        continue
    try:
        subprocess.run([p, "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
        FFMPEG_BINARY = p
        break
    except Exception:
        continue
if FFMPEG_BINARY is None:
    logging.warning("ffmpeg binary not found. Set FFMPEG_BINARY env var or place ffmpeg in ./ffmpeg or /usr/bin/ffmpeg")

# ---------------------------
# ===== Language options =====
# ---------------------------
SOURCE_LANGS = ['English', 'Arabic', 'Spanish', 'French', 'German', 'Italian', 'Portuguese', 'Russian', 'Chinese', 'Hindi']
DUB_LANGS = SOURCE_LANGS.copy()

LANG_CODE_ASR = {
    'English': 'en',
    'Arabic': 'ar',
    'Spanish': 'es',
    'French': 'fr',
    'German': 'de',
    'Italian': 'it',
    'Portuguese': 'pt',
    'Russian': 'ru',
    'Chinese': 'zh',
    'Hindi': 'hi'
}

# Per your request: use the single multilingual voice for all languages
TTS_VOICE_SINGLE = "en-US-PhoebeMultilingualNeural"
TTS_VOICES = {lang: TTS_VOICE_SINGLE for lang in SOURCE_LANGS}

# Temporary in-memory user data storage
user_data = {}

# Initialize bot
bot = telebot.TeleBot(TELEGRAM_TOKEN)

# Flask app to receive webhook updates
app = Flask(__name__)

# ---------------------------
# ====== Helpers / Utils =====
# ---------------------------
def send_gemini_translation(text, source_lang, target_lang):
    """
    Call Gemini generative API with strict instructions to return ONLY the translated text.
    Post-process to strip common labels if model still adds them.
    """
    # Strong instruction to output only the translation text, nothing else.
    prompt_text = (
        f"Translate ONLY the following text from {source_lang} into {target_lang}.\n\n"
        "Important: Output ONLY the translated text. Do NOT include any explanatory notes, headings, "
        "labels, or delimiters like 'Here is your translation'. Do NOT add anything other than the translation.\n\n"
        f"Text:\n{text}"
    )

    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
    headers = {
        "Content-Type": "application/json",
        "X-goog-api-key": GEMINI_KEY
    }
    data = {"contents":[{"parts":[{"text": prompt_text}]}]}

    try:
        response = requests.post(url, headers=headers, json=data, timeout=60)
    except Exception as e:
        logging.exception("Gemini request failed")
        return None

    if response.status_code != 200:
        logging.error("Gemini returned non-200: %s %s", response.status_code, response.text)
        return None

    try:
        body = response.json()
        # Try typical path used earlier; add fallbacks
        translated = None
        # primary expected path:
        if 'candidates' in body and isinstance(body['candidates'], list) and len(body['candidates']) > 0:
            c = body['candidates'][0]
            # candidate content parts path
            if 'content' in c and 'parts' in c['content'] and len(c['content']['parts']) > 0:
                translated = c['content']['parts'][0].get('text')
        # fallback: sometimes top-level 'output' or other shapes:
        if not translated and 'output' in body:
            translated = body['output']
        if not translated:
            # Last resort: serialize and return something
            translated = json.dumps(body)[:3000]

        # Post-process: remove some common prefixes the model may still add
        if isinstance(translated, str):
            for prefix in ["Here is your translation:", "Translation:", "Translated text:", "Output:"]:
                if translated.strip().startswith(prefix):
                    translated = translated.strip()[len(prefix):].strip()
            translated = translated.strip()
        return translated
    except Exception as e:
        logging.exception("Error parsing Gemini response")
        return None

def check_video_size_duration(file_path):
    max_size = 20 * 1024 * 1024  # 20 MB
    max_duration = 120  # 2 minutes
    size = os.path.getsize(file_path)
    clip = VideoFileClip(file_path)
    duration = clip.duration
    clip.close()
    if size > max_size:
        return False, "File size exceeds 20 MB limit."
    if duration > max_duration:
        return False, "Video duration exceeds 2 minutes."
    return True, ""

async def generate_tts(text, output_path, voice=TTS_VOICE_SINGLE):
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)

def merge_audio_video(video_path, audio_path, output_path):
    ffmpeg_bin = FFMPEG_BINARY if FFMPEG_BINARY else "ffmpeg"
    # map streams carefully and shorted to audio length
    cmd = f'{ffmpeg_bin} -y -i "{video_path}" -i "{audio_path}" -map 0:v:0 -map 1:a:0 -c:v copy -c:a aac -shortest -movflags +faststart "{output_path}"'
    process = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if process.returncode != 0:
        logging.error("FFmpeg error: %s", process.stderr.decode())
    return process.returncode == 0

# ---------------------------
# ===== Telegram Handlers ====
# ---------------------------
@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(message.chat.id, "Fadlan video-gaaga soo dir si aan u turjumo oo cod cusub kuugu sameeyo.")

@bot.message_handler(content_types=['video'])
def handle_video(message):
    try:
        file_info = bot.get_file(message.video.file_id)
        if file_info.file_path is None:
            bot.send_message(message.chat.id, "Error: Could not process video file.")
            return
        downloaded_file = bot.download_file(file_info.file_path)
        file_path = f'temp_{message.from_user.id}.mp4'

        with open(file_path, 'wb') as f:
            f.write(downloaded_file)

        # Check size & duration
        valid, msg = check_video_size_duration(file_path)
        if not valid:
            bot.send_message(message.chat.id, f"Digniin: {msg}")
            os.remove(file_path)
            return

        # Store and ask source language via inline keyboard
        user_id = message.from_user.id
        user_data[user_id] = {'video_path': file_path}

        markup = types.InlineKeyboardMarkup(row_width=2)
        buttons = [types.InlineKeyboardButton(text=lang, callback_data=f"src|{lang}") for lang in SOURCE_LANGS]
        markup.add(*buttons)
        bot.send_message(message.chat.id, "Dooro luqadda video-ga uu ku hadlo:", reply_markup=markup)

    except Exception as e:
        logging.exception("Error handling video")
        bot.send_message(message.chat.id, "Error processing video. Please try again.")

@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    try:
        user_id = call.from_user.id
        data = call.data or ""
        if data.startswith("src|"):
            lang = data.split("|", 1)[1]
            if user_id not in user_data:
                bot.answer_callback_query(call.id, "Session expired. Please send your video again.")
                return
            user_data[user_id]['source_lang'] = lang
            # edit message to confirm and ask dubbing language
            bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id,
                                  text=f"Luqaadda la doortay: {lang}\nHadda dooro luqadda dubbing-ka:")
            markup = types.InlineKeyboardMarkup(row_width=2)
            buttons = [types.InlineKeyboardButton(text=lang2, callback_data=f"dub|{lang2}") for lang2 in DUB_LANGS]
            markup.add(*buttons)
            bot.send_message(call.message.chat.id, "Dooro luqadda dubbing-ka:", reply_markup=markup)
            bot.answer_callback_query(call.id, f"Source language set to {lang}")

        elif data.startswith("dub|"):
            lang = data.split("|", 1)[1]
            if user_id not in user_data or 'source_lang' not in user_data[user_id]:
                bot.answer_callback_query(call.id, "Session expired or source language missing. Please send video again.")
                return
            user_data[user_id]['dub_lang'] = lang
            bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id,
                                  text=f"Dubbing language set to: {lang}\nProcessing your video...")
            bot.answer_callback_query(call.id, f"Dub language set to {lang}")

            # Start processing (synchronously call async runner)
            asyncio.run(process_video(call.message.chat.id, user_data[user_id]))

    except Exception as e:
        logging.exception("Error in callback handler")
        try:
            bot.answer_callback_query(call.id, "Khalad ayaa dhacay.")
        except:
            pass

async def process_video(chat_id, data):
    video_path = data['video_path']
    source_lang = data['source_lang']
    dub_lang = data['dub_lang']

    try:
        bot.send_message(chat_id, "Uploading video for transcription...")

        # ====== ASR via AssemblyAI ======
        headers = {'authorization': ASSEMBLYAI_KEY, 'content-type': 'application/octet-stream'}
        with open(video_path, 'rb') as f:
            response = requests.post('https://api.assemblyai.com/v2/upload', headers=headers, data=f, timeout=180)

        if response.status_code not in (200, 201):
            bot.send_message(chat_id, "Failed to upload video for transcription. Please try again.")
            return

        audio_url = response.json()['upload_url']

        # Start transcription with language code
        trans_resp = requests.post(
            'https://api.assemblyai.com/v2/transcript',
            headers={'authorization': ASSEMBLYAI_KEY},
            json={'audio_url': audio_url, 'language_code': LANG_CODE_ASR.get(source_lang, 'en')}
        )

        if trans_resp.status_code not in (200, 201):
            bot.send_message(chat_id, "Failed to start transcription. Please try again.")
            return

        trans_id = trans_resp.json()['id']

        # Poll for completion with timeout and backoff
        max_retries = 60  # ~3 minutes
        retry_count = 0

        while retry_count < max_retries:
            time.sleep(3)
            status_resp = requests.get(f'https://api.assemblyai.com/v2/transcript/{trans_id}',
                                     headers={'authorization': ASSEMBLYAI_KEY}, timeout=30)

            if status_resp.status_code != 200:
                bot.send_message(chat_id, "Error checking transcription status. Please try again.")
                return

            status = status_resp.json()
            if status.get('status') == 'completed':
                text = status.get('text', '')
                break
            elif status.get('status') == 'failed':
                bot.send_message(chat_id, "ASR failed. Please try again.")
                return
            retry_count += 1

        if retry_count >= max_retries:
            bot.send_message(chat_id, "Transcription timed out. Please try again with a shorter video.")
            return

        if not text:
            bot.send_message(chat_id, "No transcript text returned.")
            return

        # ====== Translation via Gemini ======
        bot.send_message(chat_id, "Translating text...")
        translated_text = send_gemini_translation(text, source_lang, dub_lang)
        if not translated_text:
            bot.send_message(chat_id, "Translation failed. Please try again.")
            return

        # ====== TTS via EDGE_TTS (single multilingual voice) ======
        tts_path = f'tts_{chat_id}.mp3'
        voice = TTS_VOICE_SINGLE
        bot.send_message(chat_id, "Generating TTS audio...")
        await generate_tts(translated_text, tts_path, voice)

        # ====== Merge audio & video ======
        output_path = f'dubbed_{chat_id}.mp4'
        bot.send_message(chat_id, "Merging audio and video...")
        success = merge_audio_video(video_path, tts_path, output_path)
        if not success:
            bot.send_message(chat_id, "Failed to merge audio and video.")
            return

        bot.send_message(chat_id, "Halkan waa video-gaaga la turjumay/dubbed:")
        with open(output_path, 'rb') as video_file:
            bot.send_video(chat_id, video_file)

    except Exception as e:
        logging.exception("Error processing video")
        bot.send_message(chat_id, "An error occurred while processing your video. Please try again.")
    finally:
        # Clean up temp files
        tts_path = f'tts_{chat_id}.mp3'
        output_path = f'dubbed_{chat_id}.mp4'
        for f in [video_path, tts_path, output_path]:
            try:
                if os.path.exists(f):
                    os.remove(f)
            except Exception:
                pass
        if chat_id in user_data:
            del user_data[chat_id]

# ---------------------------
# ===== Webhook Server ======
# ---------------------------
@app.route('/', methods=['POST'])
def webhook_handler():
    """
    Receive Telegram update via webhook and pass to TeleBot.
    """
    try:
        json_str = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_str)
        bot.process_new_updates([update])
    except Exception as e:
        logging.exception("Failed to process incoming webhook")
    return '', 200

# Utility to set webhook at startup
def setup_webhook():
    try:
        bot.remove_webhook()
        # Set webhook. Telegram will send updates to WEBHOOK_URL.
        success = bot.set_webhook(WEBHOOK_URL)
        if not success:
            logging.error("Failed to set webhook to %s", WEBHOOK_URL)
        else:
            logging.info("Webhook set to %s", WEBHOOK_URL)
    except Exception:
        logging.exception("Error setting webhook")

# ---------------------------
# ====== Main Entrypoint =====
# ---------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Starting Telegram webhook bot...")
    setup_webhook()
    # If you run this on Render or similar, make sure the service binds to the correct port.
    # Default Flask will use port 5000; Render often uses env PORT.
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
