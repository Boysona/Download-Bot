# main.py
import os
import sys
import time
import json
import uuid
import logging
import requests
import subprocess
import threading
import yt_dlp
import re
from urllib.parse import urlparse
from moviepy.editor import VideoFileClip
import edge_tts
import asyncio
import telebot
from telebot import types
from flask import Flask, request

# ----------------------------
# Configuration (use env vars)
# ----------------------------
# Prefer environment variables for secrets. If not present, fallback to values from your original script.
BOT_TOKEN = os.environ.get("BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN") or "8371007825:AAFpp_SVygKKTR6y0PlX9W4q9LBrgwLA6b8"
ASSEMBLYAI_KEY = os.environ.get("ASSEMBLYAI_KEY") or "a356bbda79da4fd8a77a12ad819c47e2"
GEMINI_KEY = os.environ.get("GEMINI_KEY") or "AIzaSyDLxRqMWmjpLW0IRh85JwLdLcYMEWY0_kk"
WEBHOOK_URL = os.environ.get("WEBHOOK_URL") or "https://download-bot-5sv5.onrender.com"
PORT = int(os.environ.get("PORT", 8080))

# ----------------------------
# Logging
# ----------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

# ----------------------------
# Detect ffmpeg
# ----------------------------
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
if not FFMPEG_BINARY:
    logging.warning("ffmpeg not found in expected locations. Install ffmpeg or set FFMPEG_BINARY env var.")

# ----------------------------
# Languages config
# ----------------------------
SOURCE_LANGS = ['English', 'Arabic', 'Spanish', 'French', 'German', 'Italian', 'Portuguese', 'Russian', 'Chinese', 'Hindi']
DUB_LANGS = [
    "English","Arabic","Somali","Spanish","French","German","Italian","Portuguese","Russian","Chinese","Hindi","Urdu","Bengali","Punjabi",
    "Indonesian","Malay","Turkish","Vietnamese","Thai","Japanese","Korean","Persian","Swahili","Amharic","Yoruba","Hausa","Igbo","Zulu","Xhosa",
    "Afrikaans","Dutch","Polish","Czech","Slovak","Hungarian","Romanian","Bulgarian","Serbian","Croatian","Bosnian","Slovenian","Greek","Albanian",
    "Macedonian","Lithuanian","Latvian","Estonian","Finnish","Swedish","Norwegian","Danish","Icelandic","Hebrew","Nepali","Sinhala","Khmer","Lao",
    "Mongolian","Tibetan","Burmese","Filipino","Tagalog","Catalan","Basque","Galician","Ukrainian","Belarusian","Georgian","Armenian","Azerbaijani",
    "Kazakh","Uzbek","Turkmen","Kyrgyz","Tajik","Malayalam","Kannada","Tamil","Telugu","Marathi","Gujarati","Odia","Assamese","Sindhi","Kurdish",
    "Pashto","Kinyarwanda","Kirundi","Sesotho","Setswana","Lingala","Shona","Tigrinya","Fijian","Samoan","Tongan","Haitian Creole","Luxembourgish"
]

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

TTS_VOICE_SINGLE = "en-US-PhoebeMultilingualNeural"

# ----------------------------
# Bot + Flask app
# ----------------------------
bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# ephemeral per-user session store
user_sessions = {}

# Supported domains for downloader
SUPPORTED_DOMAINS = [
    'youtube.com', 'youtu.be',
    'instagram.com', 'instagr.am',
    'tiktok.com',
    'twitter.com', 'x.com',
    'facebook.com', 'fb.watch',
    'reddit.com',
    'pinterest.com',
    'likee.video',
    'snapchat.com',
    'threads.net'
]

def is_supported_url(url):
    try:
        domain = urlparse(url).netloc.lower()
        return any(supported in domain for supported in SUPPORTED_DOMAINS)
    except Exception:
        return False

# ----------------------------
# Utility functions
# ----------------------------
def check_video_size_duration(file_path):
    try:
        max_size = 20 * 1024 * 1024  # 20 MB (Telegram file upload limit for bots is larger for files, but keep conservative)
        max_duration = 120  # seconds
        size = os.path.getsize(file_path)
        clip = VideoFileClip(file_path)
        duration = clip.duration
        clip.close()
        if size > max_size:
            return False, "File size exceeds 20 MB limit."
        if duration > max_duration:
            return False, "Video duration exceeds 2 minutes."
        return True, ""
    except Exception as e:
        logging.exception("check_video_size_duration error")
        return False, "Unable to inspect video (maybe corrupted or unsupported)."

def merge_audio_video(video_path, audio_path, output_path):
    ffmpeg_bin = FFMPEG_BINARY or "ffmpeg"
    cmd = [
        ffmpeg_bin, "-y",
        "-i", video_path,
        "-i", audio_path,
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac",
        "-shortest",
        "-movflags", "+faststart",
        output_path
    ]
    process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if process.returncode != 0:
        logging.error("FFmpeg merge error: %s", process.stderr.decode(errors='ignore'))
        return False
    return True

def transcode_video(input_path, output_path, target_label):
    ffmpeg_bin = FFMPEG_BINARY or "ffmpeg"
    if target_label == "1080p":
        scale = "1920:1080"
    elif target_label == "720p":
        scale = "1280:720"
    elif target_label == "640p":
        scale = "640:360"
    elif target_label == "256p":
        scale = "426:256"
    else:
        scale = "640:360"
    cmd = [
        ffmpeg_bin, "-y",
        "-i", input_path,
        "-vf", f"scale={scale}",
        "-c:v", "libx264",
        "-crf", "23",
        "-preset", "veryfast",
        "-c:a", "aac",
        "-movflags", "+faststart",
        output_path
    ]
    process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if process.returncode != 0:
        logging.error("FFmpeg transcode error: %s", process.stderr.decode(errors='ignore'))
        return False
    return True

async def generate_tts_async(text, output_path, voice=TTS_VOICE_SINGLE):
    communicator = edge_tts.Communicate(text, voice)
    await communicator.save(output_path)

def safe_remove(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass

# ----------------------------
# External service helpers
# ----------------------------
def send_gemini_translation(text, source_lang, target_lang):
    prompt_text = (
        f"Translate ONLY the following text from {source_lang} into {target_lang}.\n\n"
        "Important: Output ONLY the translated text. Do NOT include any explanatory notes, headings, "
        "labels, or delimiters. Do NOT add anything other than the translation.\n\n"
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
    except Exception:
        logging.exception("Gemini request failed")
        return None
    if response.status_code != 200:
        logging.error("Gemini returned non-200: %s %s", response.status_code, response.text)
        return None
    try:
        body = response.json()
        translated = None
        if 'candidates' in body and isinstance(body['candidates'], list) and len(body['candidates']) > 0:
            c = body['candidates'][0]
            if 'content' in c and 'parts' in c['content'] and len(c['content']['parts']) > 0:
                translated = c['content']['parts'][0].get('text')
        if not translated and 'output' in body:
            translated = body['output']
        if not translated:
            translated = json.dumps(body)[:3000]
        if isinstance(translated, str):
            for prefix in ["Here is your translation:", "Translation:", "Translated text:", "Output:"]:
                if translated.strip().startswith(prefix):
                    translated = translated.strip()[len(prefix):].strip()
            translated = translated.strip()
        return translated
    except Exception:
        logging.exception("Error parsing Gemini response")
        return None

# ----------------------------
# YouTube/other downloader helpers (yt-dlp)
# ----------------------------
def extract_video_info(url):
    ydl_opts = {'quiet': True, 'no_warnings': True, 'format': 'best'}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info
    except Exception:
        logging.exception("extract_video_info failed")
        return None

def download_video_to_folder(url, folder='downloads'):
    os.makedirs(folder, exist_ok=True)
    ydl_opts = {
        'format': 'mp4',
        'quiet': True,
        'no_warnings': True,
        'outtmpl': os.path.join(folder, '%(id)s.%(ext)s'),
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            return filename
    except Exception:
        logging.exception("download_video_to_folder failed")
        return None

def clean_filename(filename):
    return re.sub(r'[^\w\-_. ]', '', filename)

# ----------------------------
# Telegram handlers
# ----------------------------
@bot.message_handler(commands=['start', 'help'])
def cmd_start(message):
    text = (
        "Salaam! Waxaan isku dhafnay laba adeeg: (1) dubbing / translate video iyo (2) link downloader.\n\n"
        "➤ Si aad u turjunto/dub gareyso: soo dir video file kaaga.\n"
        "➤ Si aad u soo dejiso link: dir link ka YouTube/Instagram/TikTok/iwm.\n\n"
        "Bot-ka wuxuu isticmaalaa fariin status oo la edit gareeyo intii hawshu socoto, si chat-ka aanu u buuxin farriimo badan."
    )
    bot.send_message(message.chat.id, text)

# Handle URLs (download)
@bot.message_handler(func=lambda m: isinstance(m.text, str) and is_supported_url(m.text.strip()), content_types=['text'])
def handle_supported_url(message):
    url = message.text.strip()
    user_id = message.from_user.id
    chat_id = message.chat.id
    try:
        processing = bot.send_message(chat_id, "🔍 Processing link — checking info...")
        info = extract_video_info(url)
        if not info:
            bot.edit_message_text("❌ Failed to extract video info. The video may be private or unsupported.", chat_id=chat_id, message_id=processing.message_id)
            bot.delete_message(chat_id, processing.message_id)
            return
        title = info.get('title', 'Untitled')
        duration = info.get('duration', 0)
        uploader = info.get('uploader', 'Unknown')
        details = f"📹 Title: {title}\n⏱ Duration: {duration} sec\n📤 Uploader: {uploader}\n\n⬇️ Downloading..."
        bot.edit_message_text(details, chat_id=chat_id, message_id=processing.message_id)
        video_path = download_video_to_folder(url)
        if not video_path or not os.path.exists(video_path):
            bot.edit_message_text("❌ Failed to download video. It might be blocked or removed.", chat_id=chat_id, message_id=processing.message_id)
            bot.delete_message(chat_id, processing.message_id)
            return
        with open(video_path, 'rb') as vf:
            bot.send_video(chat_id, vf, caption=f"✅ Downloaded: {clean_filename(title)}")
        # cleanup
        try:
            safe_remove(video_path)
        except:
            pass
        bot.delete_message(chat_id, processing.message_id)
    except Exception:
        logging.exception("Error in handle_supported_url")
        try:
            bot.send_message(chat_id, "⚠️ Error processing link. Please try again later.")
        except:
            pass

# Handle incoming video files (to dub/translate)
@bot.message_handler(content_types=['video', 'document'])
def handle_video_file(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    try:
        # get file info whether video or document
        if hasattr(message, 'video') and message.video:
            file_id = message.video.file_id
        elif hasattr(message, 'document') and message.document:
            file_id = message.document.file_id
        else:
            bot.send_message(chat_id, "Send a video file (mp4/mkv) or a supported document.")
            return
        file_info = bot.get_file(file_id)
        if not file_info or not getattr(file_info, 'file_path', None):
            bot.send_message(chat_id, "Unable to retrieve the file. Try again.")
            return
        processing_msg = bot.send_message(chat_id, "🔁 Downloading your video — please wait...")
        downloaded = bot.download_file(file_info.file_path)
        local_fname = f"tmp_{user_id}_{uuid.uuid4().hex}.mp4"
        with open(local_fname, 'wb') as f:
            f.write(downloaded)
        valid, reason = check_video_size_duration(local_fname)
        if not valid:
            bot.edit_message_text(f"❌ {reason}", chat_id=chat_id, message_id=processing_msg.message_id)
            safe_remove(local_fname)
            # delete status msg after short delay to keep chat clean
            time.sleep(2)
            try:
                bot.delete_message(chat_id, processing_msg.message_id)
            except:
                pass
            return
        # store session
        user_sessions[user_id] = {
            'video_path': local_fname,
            'chat_id': chat_id,
            'processing_message_id': processing_msg.message_id
        }
        # ask for source language
        markup = types.InlineKeyboardMarkup(row_width=3)
        buttons = [types.InlineKeyboardButton(text=lang, callback_data=f"src|{lang}") for lang in SOURCE_LANGS]
        markup.add(*buttons)
        bot.edit_message_text("Select the original language spoken in the video:", chat_id=chat_id, message_id=processing_msg.message_id, reply_markup=markup)
    except Exception:
        logging.exception("Error in handle_video_file")
        try:
            bot.send_message(chat_id, "⚠️ An error occurred handling your video. Try again.")
        except:
            pass

# Callback handler for inline buttons (source, dub, quality)
@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    try:
        user_id = call.from_user.id
        data = call.data or ""
        session = user_sessions.get(user_id)
        if data.startswith("src|"):
            lang = data.split("|", 1)[1]
            if not session:
                bot.answer_callback_query(call.id, "Session expired. Please send your video again.")
                return
            session['source_lang'] = lang
            bot.answer_callback_query(call.id, f"Source language set: {lang}")
            # edit status message to ask dub language
            chat_id = session['chat_id']
            pid = session['processing_message_id']
            markup = types.InlineKeyboardMarkup(row_width=3)
            buttons = [types.InlineKeyboardButton(text=lang2, callback_data=f"dub|{lang2}") for lang2 in DUB_LANGS]
            markup.add(*buttons)
            bot.edit_message_text(f"Source language: {lang}\n\nChoose the dubbing language:", chat_id=chat_id, message_id=pid, reply_markup=markup)
        elif data.startswith("dub|"):
            lang = data.split("|", 1)[1]
            if not session or 'source_lang' not in session:
                bot.answer_callback_query(call.id, "Session expired or missing source language. Send video again.")
                return
            session['dub_lang'] = lang
            bot.answer_callback_query(call.id, f"Dubbing language set: {lang}")
            # start processing in background thread
            chat_id = session['chat_id']
            pid = session['processing_message_id']
            bot.edit_message_text(f"Preparing to process your video...\nSource: {session['source_lang']}\nTarget: {lang}", chat_id=chat_id, message_id=pid)
            thread = threading.Thread(target=process_video_thread, args=(user_id,), daemon=True)
            thread.start()
        elif data.startswith("quality|"):
            quality = data.split("|", 1)[1]
            if not session or 'final_output' not in session:
                bot.answer_callback_query(call.id, "No processed video available. Process a new video.")
                return
            bot.answer_callback_query(call.id, f"Preparing {quality} file...")
            chat_id = session['chat_id']
            pid = session['processing_message_id']
            bot.edit_message_text(f"Transcoding to {quality} — please wait...", chat_id=chat_id, message_id=pid)
            input_path = session['final_output']
            out_path = f"final_{user_id}_{quality}.mp4"
            success = transcode_video(input_path, out_path, quality)
            if not success:
                bot.edit_message_text("Transcoding failed. Sending original file instead.", chat_id=chat_id, message_id=pid)
                out_path = input_path
            try:
                with open(out_path, 'rb') as vf:
                    bot.send_video(chat_id, vf, caption=f"🎬 Your video ({quality})")
            except Exception:
                bot.send_message(chat_id, "Failed to send video. Try again.")
            # cleanup
            try:
                safe_remove(out_path)
                safe_remove(input_path)
                safe_remove(session.get('tts_path'))
            except:
                pass
            try:
                bot.delete_message(chat_id, pid)
            except:
                pass
            user_sessions.pop(user_id, None)
    except Exception:
        logging.exception("Error in callback_query")
        try:
            bot.answer_callback_query(call.id, "An unexpected error occurred.")
        except:
            pass

# ----------------------------
# Processing thread
# ----------------------------
def process_video_thread(user_id):
    """
    Runs in background thread to keep telebot responsive.
    Uses synchronous requests for ASR/translation and asyncio for TTS.
    """
    session = user_sessions.get(user_id)
    if not session:
        return
    chat_id = session['chat_id']
    pid = session['processing_message_id']
    video_path = session['video_path']
    try:
        # Step 1: upload to AssemblyAI
        bot.edit_message_text("Status: Uploading video for transcription...", chat_id=chat_id, message_id=pid)
        headers = {'authorization': ASSEMBLYAI_KEY, 'content-type': 'application/octet-stream'}
        with open(video_path, 'rb') as f:
            resp = requests.post('https://api.assemblyai.com/v2/upload', headers=headers, data=f, timeout=180)
        if resp.status_code not in (200, 201):
            bot.edit_message_text("Status: Failed to upload for transcription.", chat_id=chat_id, message_id=pid)
            time.sleep(2)
            try: bot.delete_message(chat_id, pid)
            except: pass
            return
        audio_url = resp.json().get('upload_url')
        bot.edit_message_text("Status: Submitted for transcription...", chat_id=chat_id, message_id=pid)
        trans_resp = requests.post(
            'https://api.assemblyai.com/v2/transcript',
            headers={'authorization': ASSEMBLYAI_KEY},
            json={'audio_url': audio_url, 'language_code': LANG_CODE_ASR.get(session.get('source_lang'), 'en')}
        )
        if trans_resp.status_code not in (200, 201):
            bot.edit_message_text("Status: Failed to start transcription.", chat_id=chat_id, message_id=pid)
            time.sleep(2)
            try: bot.delete_message(chat_id, pid)
            except: pass
            return
        trans_id = trans_resp.json().get('id')
        # Poll for ASR result
        max_retries = 60
        retry = 0
        transcript_text = ""
        while retry < max_retries:
            time.sleep(3)
            st = requests.get(f'https://api.assemblyai.com/v2/transcript/{trans_id}', headers={'authorization': ASSEMBLYAI_KEY}, timeout=30)
            if st.status_code != 200:
                bot.edit_message_text("Status: Error checking transcription.", chat_id=chat_id, message_id=pid)
                time.sleep(2)
                try: bot.delete_message(chat_id, pid)
                except: pass
                return
            j = st.json()
            status = j.get('status')
            if status == 'completed':
                transcript_text = j.get('text', '')
                break
            elif status == 'failed':
                bot.edit_message_text("Status: Transcription failed.", chat_id=chat_id, message_id=pid)
                time.sleep(2)
                try: bot.delete_message(chat_id, pid)
                except: pass
                return
            retry += 1
        if retry >= max_retries:
            bot.edit_message_text("Status: Transcription timed out. Try a shorter video.", chat_id=chat_id, message_id=pid)
            time.sleep(2)
            try: bot.delete_message(chat_id, pid)
            except: pass
            return
        if not transcript_text:
            bot.edit_message_text("Status: No transcript returned.", chat_id=chat_id, message_id=pid)
            time.sleep(2)
            try: bot.delete_message(chat_id, pid)
            except: pass
            return

        # Step 2: Translate via Gemini
        bot.edit_message_text("Status: Translating text...", chat_id=chat_id, message_id=pid)
        translated = send_gemini_translation(transcript_text, session.get('source_lang'), session.get('dub_lang'))
        if not translated:
            bot.edit_message_text("Status: Translation failed.", chat_id=chat_id, message_id=pid)
            time.sleep(2)
            try: bot.delete_message(chat_id, pid)
            except: pass
            return

        # Step 3: TTS generation (edge-tts)
        bot.edit_message_text("Status: Generating speech audio (TTS)...", chat_id=chat_id, message_id=pid)
        tts_path = f"tts_{user_id}_{uuid.uuid4().hex}.mp3"
        try:
            asyncio.run(generate_tts_async(translated, tts_path))
        except Exception:
            logging.exception("TTS generation failed")
            bot.edit_message_text("Status: TTS generation failed.", chat_id=chat_id, message_id=pid)
            time.sleep(2)
            try: bot.delete_message(chat_id, pid)
            except: pass
            return

        # Step 4: Merge audio & video
        bot.edit_message_text("Status: Merging new audio with video...", chat_id=chat_id, message_id=pid)
        output_path = f"dubbed_{user_id}_{uuid.uuid4().hex}.mp4"
        success = merge_audio_video(video_path, tts_path, output_path)
        if not success:
            bot.edit_message_text("Status: Failed to merge audio and video.", chat_id=chat_id, message_id=pid)
            time.sleep(2)
            try: bot.delete_message(chat_id, pid)
            except: pass
            return

        # Step 5: Present quality options (edit the same message to keep chat tidy)
        session['final_output'] = output_path
        session['tts_path'] = tts_path
        markup = types.InlineKeyboardMarkup(row_width=2)
        qbuttons = [
            types.InlineKeyboardButton(text="1080p", callback_data="quality|1080p"),
            types.InlineKeyboardButton(text="720p", callback_data="quality|720p"),
            types.InlineKeyboardButton(text="640p", callback_data="quality|640p"),
            types.InlineKeyboardButton(text="256p", callback_data="quality|256p")
        ]
        markup.add(*qbuttons)
        bot.edit_message_text("Status: Done. Select output quality (this message will be replaced):", chat_id=chat_id, message_id=pid, reply_markup=markup)
    except Exception:
        logging.exception("process_video_thread error")
        try:
            bot.edit_message_text("Status: An unexpected error occurred during processing.", chat_id=chat_id, message_id=pid)
            time.sleep(2)
            bot.delete_message(chat_id, pid)
        except:
            pass
    finally:
        # keep session so user can select quality; cleanup of files happens after sending final
        pass

# ----------------------------
# Webhook / Flask endpoints
# ----------------------------
@app.route('/', methods=['GET'])
def index():
    return "✅ Bot running", 200

@app.route(f"/{BOT_TOKEN}", methods=['POST'])
def webhook_handler():
    try:
        json_str = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_str)
        bot.process_new_updates([update])
    except Exception:
        logging.exception("Failed to process webhook update")
        return "error", 500
    return '', 200

def setup_webhook():
    try:
        bot.remove_webhook()
        # Set webhook to WEBHOOK_URL + "/" + BOT_TOKEN
        full_url = WEBHOOK_URL.rstrip('/') + '/' + BOT_TOKEN
        ok = bot.set_webhook(full_url)
        if not ok:
            logging.error("Failed to set webhook to %s", full_url)
        else:
            logging.info("Webhook set to %s", full_url)
    except Exception:
        logging.exception("Error setting webhook")

# ----------------------------
# Run
# ----------------------------
if __name__ == "__main__":
    setup_webhook()
    logging.info("Starting Flask app on port %s ...", PORT)
    # ensure downloads folder exists
    os.makedirs("downloads", exist_ok=True)
    app.run(host="0.0.0.0", port=PORT)
