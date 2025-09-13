import os
import requests
import telebot
from telebot import types
from flask import Flask, request, jsonify
import yt_dlp
import re
from urllib.parse import urlparse
import uuid
import subprocess
import logging
import tempfile
import shutil

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

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

app = Flask(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8136008912:AAHwM1ZBZ2WxgCnFpRA0MC_EIr9KcRQiF3c")
BASE_WEBHOOK_HOST = os.environ.get("WEBHOOK_HOST", "https://tts-bot-2.onrender.com")
WEBHOOK_URL = BASE_WEBHOOK_HOST.rstrip("/") + "/" + BOT_TOKEN

bot = telebot.TeleBot(BOT_TOKEN)

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

def extract_video_info(url):
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'format': 'bestvideo+bestaudio/best'
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            return info
        except Exception as e:
            logging.error("extract_video_info error %s", e)
            return None

def download_video_to_path(url, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    unique_id = uuid.uuid4().hex
    outtmpl = os.path.join(out_dir, f"{unique_id}.%(ext)s")
    ydl_opts = {
        'format': 'bestvideo+bestaudio/best',
        'quiet': True,
        'no_warnings': True,
        'outtmpl': outtmpl,
        'merge_output_format': 'mp4'
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            if not os.path.exists(filename):
                base, _ = os.path.splitext(filename)
                candidates = [p for p in os.listdir(out_dir) if p.startswith(os.path.basename(base))]
                if candidates:
                    filename = os.path.join(out_dir, candidates[0])
            return filename
        except Exception as e:
            logging.error("download_video_to_path error %s", e)
            return None

def safe_fn(name):
    return re.sub(r'[^\w\-. ]', '', name)

def ensure_mp4_with_ffmpeg(input_path):
    if FFMPEG_BINARY is None:
        return input_path
    tmpdir = tempfile.mkdtemp(prefix="ffproc_")
    try:
        base_out = os.path.join(tmpdir, "out.mp4")
        cmd = [
            FFMPEG_BINARY,
            "-y",
            "-i",
            input_path,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            base_out
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=600)
        if os.path.exists(base_out) and os.path.getsize(base_out) > 0:
            final_path = input_path + ".ffmpg.mp4"
            shutil.move(base_out, final_path)
            return final_path
        return input_path
    except Exception as e:
        logging.error("ensure_mp4_with_ffmpeg error %s", e)
        return input_path
    finally:
        try:
            for f in os.listdir(tmpdir):
                try:
                    os.remove(os.path.join(tmpdir, f))
                except Exception:
                    pass
            os.rmdir(tmpdir)
        except Exception:
            pass

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    text = f"Send me a video link (YouTube, TikTok, Instagram, Twitter/X, Facebook, Reddit, Pinterest, Snapchat, Threads) and I will download it."
    bot.reply_to(message, text)

@bot.message_handler(func=lambda message: True, content_types=['text'])
def handle_message(message):
    text = message.text.strip()
    if not is_supported_url(text):
        bot.reply_to(message, "Unsupported URL. Send a link from supported platforms.")
        return
    processing_msg = bot.reply_to(message, "Processing your link, please wait...")
    try:
        info = extract_video_info(text)
        if not info:
            bot.edit_message_text("Failed to get video information. The link might be invalid or private.", chat_id=message.chat.id, message_id=processing_msg.message_id)
            return
        title = info.get('title', 'Untitled')
        duration = info.get('duration', 0)
        uploader = info.get('uploader', 'Unknown')
        details = f"Title: {title}\nDuration: {duration} seconds\nUploader: {uploader}\n\nDownloading..."
        bot.edit_message_text(details, chat_id=message.chat.id, message_id=processing_msg.message_id)
        outdir = "downloads"
        os.makedirs(outdir, exist_ok=True)
        video_path = download_video_to_path(text, outdir)
        if not video_path or not os.path.exists(video_path):
            bot.edit_message_text("Failed to download the video. Please try again later.", chat_id=message.chat.id, message_id=processing_msg.message_id)
            return
        processed_path = ensure_mp4_with_ffmpeg(video_path)
        send_path = processed_path if os.path.exists(processed_path) else video_path
        filesize_mb = os.path.getsize(send_path) / (1024*1024)
        if filesize_mb > 50:
            note = f"Downloaded ({safe_fn(title)}) but file is {filesize_mb:.2f} MB. Sending may fail. Uploading to server and sending as file..."
            bot.edit_message_text(note, chat_id=message.chat.id, message_id=processing_msg.message_id)
        with open(send_path, 'rb') as vf:
            bot.send_video(message.chat.id, vf, caption=f"{title}\nDownloaded by @{bot.get_me().username}", reply_to_message_id=message.message_id)
        try:
            bot.delete_message(message.chat.id, processing_msg.message_id)
        except Exception:
            pass
        try:
            if processed_path != video_path and os.path.exists(processed_path):
                os.remove(processed_path)
        except Exception:
            pass
        try:
            if os.path.exists(video_path):
                os.remove(video_path)
        except Exception:
            pass
    except Exception as e:
        logging.error("handle_message exception %s", e)
        try:
            bot.edit_message_text("An error occurred while processing your request. Please try again later.", chat_id=message.chat.id, message_id=processing_msg.message_id)
        except Exception:
            pass

@app.route('/')
def index():
    return "Bot is running", 200

@app.route('/set_webhook', methods=['GET'])
def set_webhook_endpoint():
    try:
        bot.remove_webhook()
        bot.set_webhook(url=WEBHOOK_URL)
        return jsonify({"status": "webhook set", "url": WEBHOOK_URL}), 200
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500

@app.route(f'/{BOT_TOKEN}', methods=['POST'])
def webhook():
    try:
        json_str = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_str)
        bot.process_new_updates([update])
    except Exception as e:
        logging.error("Webhook Error: %s", e)
        return f"Webhook Error: {e}", 500
    return '', 200

if __name__ == "__main__":
    os.makedirs("downloads", exist_ok=True)
    try:
        bot.remove_webhook()
        bot.set_webhook(url=WEBHOOK_URL)
    except Exception as e:
        logging.error("Failed to set webhook %s", e)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
