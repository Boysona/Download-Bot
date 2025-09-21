import os
import subprocess
import logging
import requests
import telebot
from telebot import types
from flask import Flask, request
import yt_dlp
import re
from urllib.parse import urlparse

app = Flask(__name__)

BOT_TOKEN = '8136008912:AAH2gyaMSE5jQSUxh2dXkYQVFo3f8w8Ir4M'
WEBHOOK_URL = 'excess-roundworm-wwmahe-08dde51d.koyeb.app/' + '/' + BOT_TOKEN

bot = telebot.TeleBot(BOT_TOKEN)

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
    logging.warning("ffmpeg binary not found. yt_dlp might fail for audio/video conversions.")
else:
    yt_dlp.utils.bug_reports_message = lambda: ""
    yt_dlp.YoutubeDL({'ffmpeg_location': FFMPEG_BINARY})

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

user_choices = {}

def is_supported_url(url):
    try:
        domain = urlparse(url).netloc.lower()
        return any(supported in domain for supported in SUPPORTED_DOMAINS)
    except:
        return False

def extract_video_info(url):
    ydl_opts = {'quiet': True, 'no_warnings': True, 'format': 'best', 'ffmpeg_location': FFMPEG_BINARY}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            return info
        except Exception as e:
            print(f"Error extracting video info: {e}")
            return None

def download_video(url, format_id=None, is_audio=False):
    create_downloads_folder()
    if is_audio:
        ydl_opts = {
            'format': 'bestaudio/best',
            'quiet': True,
            'no_warnings': True,
            'outtmpl': f'downloads/%(id)s.%(ext)s',
            'postprocessors': [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3','preferredquality': '192'}],
            'ffmpeg_location': FFMPEG_BINARY
        }
    elif format_id:
        ydl_opts = {'format': format_id,'quiet': True,'no_warnings': True,'outtmpl': f'downloads/%(id)s.%(ext)s','ffmpeg_location': FFMPEG_BINARY}
    else:
        ydl_opts = {'format': 'best','quiet': True,'no_warnings': True,'outtmpl': f'downloads/%(id)s.%(ext)s','ffmpeg_location': FFMPEG_BINARY}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            if is_audio:
                filename = os.path.splitext(filename)[0] + '.mp3'
            return filename
        except Exception as e:
            print(f"Error downloading video: {e}")
            return None

def create_downloads_folder():
    if not os.path.exists('downloads'):
        os.makedirs('downloads')

def create_format_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=3)
    markup.add(
        types.InlineKeyboardButton("144p", callback_data="format_144"),
        types.InlineKeyboardButton("240p", callback_data="format_240"),
        types.InlineKeyboardButton("360p", callback_data="format_360")
    )
    markup.add(
        types.InlineKeyboardButton("480p", callback_data="format_480"),
        types.InlineKeyboardButton("720p", callback_data="format_720"),
        types.InlineKeyboardButton("1080p", callback_data="format_1080")
    )
    markup.add(types.InlineKeyboardButton("MP3", callback_data="format_mp3"))
    return markup

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    first_name = message.from_user.first_name
    welcome_text = f"""
🌟 Welcome, {first_name}! 🌟

Send me a link from:
- YouTube
- Instagram
- TikTok
- Twitter/X
- Facebook
- Reddit
- Pinterest
- Likee
- Snapchat
- Threads

to download media in various formats.
"""
    bot.reply_to(message, welcome_text)

@bot.message_handler(func=lambda message: True, content_types=['text'])
def handle_message(message):
    text = message.text
    if not is_supported_url(text):
        bot.reply_to(message, "❌ Unsupported URL. Please send a valid video link from supported platforms.")
        return
    processing_msg = bot.reply_to(message, "🔍 Processing your link, please wait...")
    try:
        video_info = extract_video_info(text)
        if not video_info:
            bot.edit_message_text("❌ Failed to get video information.",
                                  chat_id=message.chat.id,
                                  message_id=processing_msg.message_id)
            return
        title = video_info.get('title', 'Untitled Video')
        duration = video_info.get('duration', 0)
        uploader = video_info.get('uploader', 'Unknown')
        user_choices[message.chat.id] = {'url': text, 'title': title, 'message_id': message.message_id}
        details_text = f"""
📹 Video Details:
- Title: {title}
- Duration: {duration} seconds
- Uploader: {uploader}

⬇️ Select download format:
"""
        bot.edit_message_text(details_text,
                              chat_id=message.chat.id,
                              message_id=processing_msg.message_id,
                              reply_markup=create_format_keyboard())
    except Exception as e:
        print(f"Error handling message: {e}")
        bot.edit_message_text("❌ An error occurred. Please try again later.",
                              chat_id=message.chat.id,
                              message_id=processing_msg.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('format_'))
def handle_format_selection(call):
    try:
        chat_id = call.message.chat.id
        format_type = call.data.split('_')[1]
        if chat_id not in user_choices:
            bot.answer_callback_query(call.id, "❌ Session expired. Please send the link again.")
            return
        url = user_choices[chat_id]['url']
        title = user_choices[chat_id]['title']
        original_message_id = user_choices[chat_id]['message_id']
        bot.answer_callback_query(call.id, f"⏬ Downloading in {format_type} format...")
        bot.edit_message_text(f"⬇️ Downloading in {format_type} format... Please wait.",
                              chat_id=chat_id,
                              message_id=call.message.message_id)
        if format_type == 'mp3':
            file_path = download_video(url, is_audio=True)
            file_caption = f"🎵 {title} \n\n✅ Downloaded as MP3"
        else:
            format_map = {
                '144': 'worst[height<=144]',
                '240': 'worst[height<=240]',
                '360': 'worst[height<=360]',
                '480': 'worst[height<=480]',
                '720': 'best[height<=720]',
                '1080': 'best[height<=1080]'
            }
            format_id = format_map.get(format_type, 'best')
            file_path = download_video(url, format_id=format_id)
            file_caption = f"🎥 {title} \n\n✅ Downloaded in {format_type}p"
        if not file_path or not os.path.exists(file_path):
            bot.edit_message_text("❌ Failed to download the media.",
                                  chat_id=chat_id,
                                  message_id=call.message.message_id)
            return
        if format_type == 'mp3':
            with open(file_path, 'rb') as audio_file:
                bot.send_audio(chat_id, audio_file, caption=file_caption, reply_to_message_id=original_message_id)
        else:
            with open(file_path, 'rb') as video_file:
                bot.send_video(chat_id, video_file, caption=file_caption, reply_to_message_id=original_message_id)
        try:
            os.remove(file_path)
        except:
            pass
        bot.delete_message(chat_id, call.message.message_id)
        if chat_id in user_choices:
            del user_choices[chat_id]
    except Exception as e:
        print(f"Error in callback handler: {e}")
        bot.answer_callback_query(call.id, "❌ An error occurred. Please try again.")

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
        print(f"Webhook Error: {e}")
        return f"Webhook Error: {e}", 500
    return '', 200

if __name__ == "__main__":
    create_downloads_folder()
    bot.remove_webhook()
    bot.set_webhook(url=WEBHOOK_URL)
    print(f"✅ Webhook set to: {WEBHOOK_URL}")
    app.run(host="0.0.0.0", port=8080)
