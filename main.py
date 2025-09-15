#!/usr/bin/env python3
import os
import re
import uuid
import glob
import time
import logging
import subprocess
import yt_dlp
import telebot
from telebot import types
from flask import Flask, request
from urllib.parse import urlparse

# ---------- FFMPEG detection (user-provided snippet, integrated) ----------
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
else:
    logging.info(f"ffmpeg found at: {FFMPEG_BINARY}")

# Ensure yt-dlp can find ffmpeg when required:
if FFMPEG_BINARY:
    os.environ["FFMPEG_BINARY"] = FFMPEG_BINARY

# ---------- Config ----------
BOT_TOKEN = '8136008912:AAHwM1ZBZ2WxgCnFpRA0MC_EIr9KcRQiF3c'  # replace if needed
WEBHOOK_URL = 'https://download-bot-5sv5.onrender.com' + '/' + BOT_TOKEN  # change to your domain if needed

DOWNLOAD_DIR = 'downloads'
MAX_TELEGRAM_FILESIZE = 2 * 1024 * 1024 * 1024  # 2GB Telegram limit (practical)

# ---------- Setup ----------
app = Flask(__name__)
bot = telebot.TeleBot(BOT_TOKEN)
create_logger = logging.getLogger()
create_logger.setLevel(logging.INFO)

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

# In-memory session store for callback handling
DOWNLOAD_SESSIONS = {}

# ---------- Helpers ----------
def is_supported_url(url):
    try:
        domain = urlparse(url).netloc.lower()
        return any(supported in domain for supported in SUPPORTED_DOMAINS)
    except:
        return False

def clean_filename(filename):
    return re.sub(r'[^\w\-_. ]', '', filename)

def create_downloads_folder():
    if not os.path.exists(DOWNLOAD_DIR):
        os.makedirs(DOWNLOAD_DIR)

def find_downloaded_file(video_id):
    """Finds file in downloads that starts with video_id.*"""
    pattern = os.path.join(DOWNLOAD_DIR, f"{video_id}.*")
    matches = glob.glob(pattern)
    if matches:
        # return the first match (usually correct)
        return matches[0]
    return None

def extract_video_info(url):
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        # ensure ffmpeg location is known to yt-dlp
        'ffmpeg_location': FFMPEG_BINARY if FFMPEG_BINARY else None,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            return info
        except Exception as e:
            logging.exception(f"Error extracting video info: {e}")
            return None

def download_with_quality(url, quality_key):
    """
    quality_key: one of '1080', '720', '480', '360', 'audio'
    returns path to downloaded file or None
    """
    create_downloads_folder()
    fmt = None
    postprocessors = None
    ydl_opts = {
        'outtmpl': os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
        'ffmpeg_location': FFMPEG_BINARY if FFMPEG_BINARY else None,
        'noplaylist': True,
    }

    if quality_key == 'audio':
        # download best audio and convert to mp3
        fmt = "bestaudio/best"
        postprocessors = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
        ydl_opts['format'] = fmt
        ydl_opts['postprocessors'] = postprocessors
    else:
        # For video we try to download separate video+audio and let ffmpeg merge (yt-dlp will handle)
        height = int(quality_key)
        # prefer bestvideo up to height plus best audio, fallback to best[height<=...]
        fmt = f"bestvideo[height<={height}]+bestaudio/best[height<={height}]"
        ydl_opts['format'] = fmt
        # allow fragment reassembly etc (defaults are fine)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=True)
            # info may be a playlist or single; ensure get id
            vid_id = info.get('id') if isinstance(info, dict) else None
            if not vid_id and 'entries' in info:
                # pick first entry
                entries = info.get('entries')
                if entries:
                    vid_id = entries[0].get('id')
            # try to find downloaded file (postprocessing may change ext)
            if vid_id:
                path = find_downloaded_file(vid_id)
                if path:
                    return path
            # fallback: try to inspect returned info
            filename = ydl.prepare_filename(info) if info else None
            if filename and os.path.exists(filename):
                return filename
            # last resort: pick latest file in downloads
            files = glob.glob(os.path.join(DOWNLOAD_DIR, "*"))
            if files:
                latest = max(files, key=os.path.getctime)
                return latest
            return None
        except Exception as e:
            logging.exception(f"Error downloading video: {e}")
            return None

# ---------- Bot handlers ----------
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    welcome_text = f"""
🌟 *Welcome to @{bot.get_me().username}* 🌟

Send me a video link from supported sites (YouTube, TikTok, Instagram, X/Twitter, Facebook, Reddit, Pinterest, Snapchat, Threads, Likee).
After I read the link I'll show quality options — choose one to start the download.

_Tip:_ Large videos may be too big to send through Telegram (limit ~2GB). Audio-only option will be much smaller.
"""
    bot.reply_to(message, welcome_text, parse_mode='Markdown')

@bot.message_handler(func=lambda message: True, content_types=['text'])
def handle_message(message):
    text = message.text.strip()
    if not is_supported_url(text):
        bot.reply_to(message, "❌ Unsupported URL. Please send a valid video link from a supported platform.")
        return

    processing_msg = bot.reply_to(message, "🔍 Processing your link, please wait...")
    try:
        info = extract_video_info(text)
        if not info:
            bot.edit_message_text("❌ Failed to get video information. The link might be invalid or private.",
                                  chat_id=message.chat.id,
                                  message_id=processing_msg.message_id)
            return

        title = clean_filename(info.get('title', 'Untitled'))
        duration = info.get('duration', 0)
        uploader = info.get('uploader', 'Unknown')
        vid_id = info.get('id', str(uuid.uuid4())[:8])

        # Create a session id to remember the URL & basic info
        session_id = uuid.uuid4().hex[:12]
        DOWNLOAD_SESSIONS[session_id] = {
            'url': text,
            'title': title,
            'id': vid_id,
            'chat_id': message.chat.id,
            'message_id': processing_msg.message_id
        }

        details_text = f"""📹 *Video Details*:
- *Title:* {title}
- *Duration:* {duration} seconds
- *Uploader:* {uploader}

Choose quality to download:
"""
        # Inline keyboard for qualities
        markup = types.InlineKeyboardMarkup(row_width=2)
        btns = [
            types.InlineKeyboardButton("1080p", callback_data=f"{session_id}|1080"),
            types.InlineKeyboardButton("720p", callback_data=f"{session_id}|720"),
            types.InlineKeyboardButton("480p", callback_data=f"{session_id}|480"),
            types.InlineKeyboardButton("360p", callback_data=f"{session_id}|360"),
            types.InlineKeyboardButton("Audio only (mp3)", callback_data=f"{session_id}|audio")
        ]
        markup.add(*btns)

        bot.edit_message_text(details_text,
                              chat_id=message.chat.id,
                              message_id=processing_msg.message_id,
                              parse_mode='Markdown',
                              reply_markup=markup)
    except Exception as e:
        logging.exception(f"Error handling message: {e}")
        bot.edit_message_text("❌ An error occurred while processing your request. Please try again later.",
                              chat_id=message.chat.id,
                              message_id=processing_msg.message_id)

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    try:
        data = call.data  # format: session_id|quality
        if '|' not in data:
            bot.answer_callback_query(call.id, "Invalid data.")
            return
        session_id, quality_key = data.split('|', 1)
        session = DOWNLOAD_SESSIONS.get(session_id)
        if not session:
            bot.answer_callback_query(call.id, "Session expired or not found. Please send the link again.")
            return

        chat_id = session['chat_id']
        orig_msg_id = session['message_id']
        url = session['url']
        title = session.get('title', 'Video')

        # Acknowledge
        bot.answer_callback_query(call.id, f"Selected: {quality_key}")
        # Update message to show we're downloading
        bot.edit_message_text(f"⬇️ Downloading *{title}*  — *{quality_key}* ...",
                              chat_id=chat_id,
                              message_id=orig_msg_id,
                              parse_mode='Markdown')

        # Download
        start_ts = time.time()
        filepath = download_with_quality(url, quality_key)
        elapsed = time.time() - start_ts
        if not filepath or not os.path.exists(filepath):
            bot.edit_message_text("❌ Failed to download the file. It might be private or unavailable.",
                                  chat_id=chat_id,
                                  message_id=orig_msg_id)
            # cleanup session
            DOWNLOAD_SESSIONS.pop(session_id, None)
            return

        # Check file size
        filesize = os.path.getsize(filepath)
        human_mb = round(filesize / (1024*1024), 2)
        if filesize >= MAX_TELEGRAM_FILESIZE:
            bot.edit_message_text(f"⚠️ Download succeeded but file is too large to send via Telegram ({human_mb} MB).",
                                  chat_id=chat_id,
                                  message_id=orig_msg_id)
            DOWNLOAD_SESSIONS.pop(session_id, None)
            return

        # Send file (audio or video)
        caption = f"🎥 *{title}*\n✅ Downloaded by @{bot.get_me().username}"
        ext = os.path.splitext(filepath)[1].lower()
        try:
            with open(filepath, 'rb') as f:
                if quality_key == 'audio' or ext in ['.mp3', '.m4a', '.aac', '.ogg', '.opus']:
                    bot.send_audio(chat_id, f, caption=caption, parse_mode='Markdown', reply_to_message_id=call.message.reply_to_message.message_id if call.message.reply_to_message else None)
                else:
                    bot.send_video(chat_id, f, caption=caption, parse_mode='Markdown', reply_to_message_id=call.message.reply_to_message.message_id if call.message.reply_to_message else None)
        except Exception as e:
            logging.exception(f"Error sending file: {e}")
            bot.edit_message_text("❌ Downloaded but failed to send file via Telegram.", chat_id=chat_id, message_id=orig_msg_id)
            # keep file for manual retrieval if needed
            DOWNLOAD_SESSIONS.pop(session_id, None)
            return
        # Delete the processing message
        try:
            bot.delete_message(chat_id, orig_msg_id)
        except Exception:
            pass

        # Clean up the file
        try:
            os.remove(filepath)
        except Exception:
            pass

        DOWNLOAD_SESSIONS.pop(session_id, None)

    except Exception as e:
        logging.exception(f"Callback handler error: {e}")
        try:
            bot.answer_callback_query(call.id, "An error occurred.")
        except:
            pass

# ---------- Flask webhook endpoint ----------
@app.route('/')
def index():
    return "✅ Bot is running via webhook!", 200

@app.route(f'/{BOT_TOKEN}', methods=['POST'])
def webhook():
    try:
        json_str = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_str)
        bot.process_new_updates([update])
    except Exception as e:
        logging.exception(f"Webhook Error: {e}")
        return f"Webhook Error: {e}", 500
    return '', 200

# ---------- Run ----------
if __name__ == "__main__":
    create_downloads_folder()
    # Remove existing webhook (safe step)
    try:
        bot.remove_webhook()
        time.sleep(0.5)
    except Exception:
        pass
    bot.set_webhook(url=WEBHOOK_URL)
    logging.info(f"✅ Webhook set to: {WEBHOOK_URL}")

    app.run(host="0.0.0.0", port=8080)
