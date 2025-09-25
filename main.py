import os
import requests
import telebot
from telebot import types
from flask import Flask, request
import yt_dlp
import re
import json
from urllib.parse import urlparse

app = Flask(__name__)

BOT_TOKEN = '8136008912:AAH2gyaMSE5jQSUxh2dXkYQVFo3f8w8Ir4M'
WEBHOOK_URL = 'linear-maire-wwmahe-6cb646a1.koyeb.app/' + '/' + BOT_TOKEN

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
    except:
        return False

def extract_video_info(url):
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'format': 'best',
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            return info
        except yt_dlp.utils.DownloadError as e:
            print(f"YTDL Download Error: {e}")
            return None
        except Exception as e:
            print(f"Error extracting video info: {e}")
            return None

def download_file(url, file_format):
    ext = 'mp4' if file_format == 'video' else 'mp3'
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]' if file_format == 'video' else 'bestaudio/best',
        'extract_audio': True if file_format == 'audio' else False,
        'audio_format': 'mp3' if file_format == 'audio' else None,
        'quiet': True,
        'no_warnings': True,
        'outtmpl': f'downloads/%(id)s.{ext}',
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            return filename
        except Exception as e:
            print(f"Error downloading {file_format}: {e}")
            return None

def create_downloads_folder():
    if not os.path.exists('downloads'):
        os.makedirs('downloads')

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    welcome_text = f"""
👋 *Ku Soo Dhawoow Bot-ka Soodejinta Fidyowga!* 
    
Iigu soo dir **Link** ka mid ah goobahan hoos ku xusan si aan kuugu soo dejiyo:

🔗 *Goobaha La Taageerayo:*
- YouTube 
- Instagram (Posts/Stories/Reels)
- TikTok
- Twitter/X
- Facebook
- Reddit
- Pinterest
- Likee
- Snapchat
- Threads

Fadlan, hubi in link-gu uu yahay mid dadweyne (Public).
"""
    bot.reply_to(message, welcome_text, parse_mode='Markdown')

@bot.message_handler(func=lambda message: is_supported_url(message.text) and message.content_types == ['text'], content_types=['text'])
def handle_link_sent(message):
    url = message.text
    processing_msg = bot.reply_to(message, "🔍 *Waan baarayaa link-gaaga, I sug in yar...* ⏳", parse_mode='Markdown')
    
    video_info = extract_video_info(url)
    
    if not video_info:
        bot.edit_message_text("❌ *Link-gu wuu xiran yahay ama ma shaqaynayo.*", 
                              chat_id=message.chat.id, 
                              message_id=processing_msg.message_id, 
                              parse_mode='Markdown')
        return

    title = video_info.get('title', 'Cimad La’aan')
    uploader = video_info.get('uploader', 'Lama Yaqaan')
    
    keyboard = types.InlineKeyboardMarkup()
    video_button = types.InlineKeyboardButton("Soo Deji Video ga", callback_data=f"download_video_{url}")
    audio_button = types.InlineKeyboardButton("Soo Deji Audio giisa", callback_data=f"download_audio_{url}")
    keyboard.add(video_button, audio_button)
    
    details_text = f"""
✅ *Link Waa La Aqbalay!*
    
*Cinwaanka:* {title}
*Soo Gudbiyaha:* {uploader}

Fadlan, dooro nuuca soo dejinta:
"""
    bot.edit_message_text(details_text, 
                          chat_id=message.chat.id, 
                          message_id=processing_msg.message_id,
                          reply_markup=keyboard,
                          parse_mode='Markdown')

@bot.message_handler(func=lambda message: True, content_types=['text'])
def handle_unsupported_link(message):
    bot.reply_to(message, "❌ *Link lama taageero ama maaha link fiidiyow. Fadlan dir link sax ah.*", parse_mode='Markdown')


@bot.callback_query_handler(func=lambda call: call.data.startswith('download_'))
def handle_download_callback(call):
    action, _, url = call.data.split('_', 2)
    file_type = action.split('_')[1] 
    
    bot.edit_message_text(f"⬇️ *Waxaan soo dejinayaa {file_type.upper()}, Ii sug wax yar...* ⏳", 
                          chat_id=call.message.chat.id, 
                          message_id=call.message.message_id,
                          parse_mode='Markdown')

    create_downloads_folder()
    
    try:
        file_path = download_file(url, file_type)
        if not file_path or not os.path.exists(file_path):
            raise Exception("Download path not found")
        
        with open(file_path, 'rb') as file_to_send:
            if file_type == 'video':
                bot.send_chat_action(call.message.chat.id, 'upload_video')
                bot.send_video(call.message.chat.id, file_to_send, 
                               caption=f"🎥 *{file_type.upper()} Waa La Soo Dejiyay!*",
                               parse_mode='Markdown',
                               reply_to_message_id=call.message.reply_to_message.message_id if call.message.reply_to_message else None)
            else:
                bot.send_chat_action(call.message.chat.id, 'upload_audio')
                bot.send_audio(call.message.chat.id, file_to_send, 
                               caption=f"🎧 *{file_type.upper()} Waa La Soo Dejiyay!*",
                               parse_mode='Markdown',
                               reply_to_message_id=call.message.reply_to_message.message_id if call.message.reply_to_message else None)
        
        bot.delete_message(call.message.chat.id, call.message.message_id)
        
    except Exception as e:
        error_message = f"❌ *Khalad ayaa dhacay intii lagu jiray soo dejinta {file_type.upper()}. Fadlan mar kale isku day.*"
        bot.edit_message_text(error_message, chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode='Markdown')
        print(f"Download Error for {file_type} on URL {url}: {e}")
        
    finally:
        if 'file_path' in locals() and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception as e:
                print(f"Error deleting file {file_path}: {e}")

@app.route('/')
def index():
    return "✅ Botku wuu shaqaynayaa (Webhoook)!", 200

@app.route(f'/{BOT_TOKEN}', methods=['POST'])
def webhook():
    try:
        json_str = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_str)
        bot.process_new_updates([update])
    except Exception as e:
        print(f"🚨 Webhook Error: {e}") 
        return f"🚨 Webhook Error: {e}", 500
    return '', 200

if __name__ == "__main__":
    create_downloads_folder()
    bot.remove_webhook()
    bot.set_webhook(url=WEBHOOK_URL)
    print(f"✅ Webhook wuu diiwaan gashan yahay: {WEBHOOK_URL}")
    app.run(host="0.0.0.0", port=int(os.environ.get('PORT', 8080)))
