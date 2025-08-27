import os
import re
import uuid
import shutil
import yt_dlp
import telebot
from telebot import types
from flask import Flask, request
from urllib.parse import urlparse

# -------------------------
# Basic config
# -------------------------
app = Flask(__name__)

# <<< NOTE: token-ka waa sida aad codsatay - ha wadaagin haddii aysan adiga kugu filnayn ama aad rabto ammaan badan >>>
BOT_TOKEN = '8136008912:AAHwM1ZBZ2WxgCnFpRA0MC_EIr9KcRQiF3c'
WEBHOOK_URL = 'https://roasted-donica-zarwga-8a730dfb.koyeb.app/' + '/' + BOT_TOKEN

bot = telebot.TeleBot(BOT_TOKEN)

# Supported domains (expanded)
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
    'threads.net',
    'dailymotion.com',
    'vimeo.com',
    'soundcloud.com',
    'bandcamp.com'
]

DOWNLOADS_DIR = "downloads"
MAX_VIDEO_SEND_DIRECT = 50 * 1024 * 1024  # 50 MB threshold for send_video vs send_document


# -------------------------
# Helpers
# -------------------------
def create_downloads_folder():
    if not os.path.exists(DOWNLOADS_DIR):
        os.makedirs(DOWNLOADS_DIR)


def is_supported_url(url):
    try:
        domain = urlparse(url).netloc.lower()
        return any(supported in domain for supported in SUPPORTED_DOMAINS)
    except Exception:
        return False


def clean_filename(filename):
    # keep safe chars
    return re.sub(r'[^\w\-. ]', '', filename)


def find_ffmpeg():
    """Return ffmpeg path if available, otherwise None"""
    return shutil.which("ffmpeg")


def extract_video_info(url, ffmpeg_path=None):
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'format': 'best',
        # don't download now
    }
    if ffmpeg_path:
        ydl_opts['ffmpeg_location'] = ffmpeg_path
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            return ydl.extract_info(url, download=False)
        except Exception as e:
            print(f"[extract_video_info] Error extracting info: {e}")
            return None


def download_video(url, ffmpeg_path=None):
    """
    Downloads the best video+audio and merges to mp4 (if possible).
    Returns absolute path to downloaded file or None.
    """
    # Use a unique temp template to avoid collisions
    out_template = os.path.join(DOWNLOADS_DIR, "%(id)s.%(ext)s")
    ydl_opts = {
        'format': 'bestvideo+bestaudio/best',
        'quiet': True,
        'no_warnings': True,
        'outtmpl': out_template,
        # merge to mp4 if possible
        'merge_output_format': 'mp4',
        # prefer ffmpeg if available
    }
    if ffmpeg_path:
        ydl_opts['ffmpeg_location'] = ffmpeg_path

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            # If merge_output_format applied, ensure extension is .mp4
            if not os.path.exists(filename):
                # try replacing extension with mp4
                base = os.path.splitext(filename)[0]
                alt = base + ".mp4"
                if os.path.exists(alt):
                    filename = alt
            return filename if os.path.exists(filename) else None
        except Exception as e:
            print(f"[download_video] Error downloading: {e}")
            return None


# -------------------------
# Telegram Handlers
# -------------------------
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    try:
        uname = bot.get_me().username
    except Exception:
        uname = ""
    welcome_text = f"""
🌟 *Welcome to @{uname}* 🌟

Send me a link from:
- YouTube, Vimeo, Dailymotion
- Instagram (Posts/Reels/Stories)
- TikTok
- Twitter/X
- Facebook
- Reddit
- Pinterest
- Likee
- Snapchat
- Threads
- SoundCloud / Bandcamp

and I will download it for you ⬇️
"""
    bot.reply_to(message, welcome_text, parse_mode='Markdown')


@bot.message_handler(func=lambda message: True, content_types=['text'])
def handle_message(message):
    text = (message.text or "").strip()
    if not text:
        return

    if not is_supported_url(text):
        bot.reply_to(message, "❌ Unsupported URL. Please send a valid video/audio link from supported platforms.")
        return

    processing_msg = bot.reply_to(message, "🔍 Processing your link, please wait...")

    try:
        create_downloads_folder()
        ffmpeg_path = find_ffmpeg()
        if not ffmpeg_path:
            print("[handle_message] ffmpeg not found on PATH. Some merges/conversions may fail if ffmpeg is required.")
        else:
            print(f"[handle_message] ffmpeg found at: {ffmpeg_path}")

        info = extract_video_info(text, ffmpeg_path=ffmpeg_path)
        if not info:
            bot.edit_message_text("❌ Failed to get video information. The link might be invalid or private.",
                                  chat_id=message.chat.id,
                                  message_id=processing_msg.message_id)
            return

        title = clean_filename(info.get('title', 'Untitled'))
        duration = info.get('duration', 0)
        uploader = info.get('uploader', 'Unknown')

        details_text = f"""
📹 *Video Details*:
- Title: {title}
- Duration: {duration} seconds
- Uploader: {uploader}

⬇️ *Downloading video...* Please wait.
"""
        bot.edit_message_text(details_text,
                              chat_id=message.chat.id,
                              message_id=processing_msg.message_id,
                              parse_mode='Markdown')

        video_path = download_video(text, ffmpeg_path=ffmpeg_path)
        if not video_path or not os.path.exists(video_path):
            bot.edit_message_text("❌ Failed to download the video. Please try again later.",
                                  chat_id=message.chat.id,
                                  message_id=processing_msg.message_id)
            return

        # Send file - if big, use send_document to be safer
        file_size = os.path.getsize(video_path)
        caption = f"🎥 *{title}*\n\n✅ Downloaded by @{bot.get_me().username}"
        try:
            with open(video_path, 'rb') as f:
                if file_size <= MAX_VIDEO_SEND_DIRECT:
                    bot.send_video(message.chat.id, f,
                                   caption=caption,
                                   parse_mode='Markdown',
                                   reply_to_message_id=message.message_id)
                else:
                    # file is large - send as document
                    bot.send_document(message.chat.id, f,
                                      caption=caption,
                                      parse_mode='Markdown',
                                      reply_to_message_id=message.message_id)
        except Exception as e:
            print(f"[handle_message] Error sending file: {e}")
            bot.edit_message_text("❌ Failed to send the file to Telegram. It might be too large or network error.",
                                  chat_id=message.chat.id,
                                  message_id=processing_msg.message_id)
            return
        finally:
            # cleanup
            try:
                os.remove(video_path)
            except Exception:
                pass

        # delete processing message
        try:
            bot.delete_message(message.chat.id, processing_msg.message_id)
        except Exception:
            pass

    except Exception as e:
        print(f"[handle_message] Unexpected error: {e}")
        try:
            bot.edit_message_text("❌ An error occurred while processing your request. Please try again later.",
                                  chat_id=message.chat.id,
                                  message_id=processing_msg.message_id)
        except Exception:
            pass


# -------------------------
# Webhook endpoints
# -------------------------
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
        print(f"[webhook] Error: {e}")
        return f"Webhook Error: {e}", 500
    return '', 200


# -------------------------
# Run
# -------------------------
if __name__ == "__main__":
    create_downloads_folder()
    bot.remove_webhook()
    bot.set_webhook(url=WEBHOOK_URL)
    print(f"✅ Webhook set to: {WEBHOOK_URL}")
    # When on Render, port 8080 is commonly used. Adjust if needed.
    app.run(host="0.0.0.0", port=8080)
