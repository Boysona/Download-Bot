#!/usr/bin/env python3
import os
import re
import uuid
import glob
import time
import logging
import subprocess
import random
import yt_dlp
import telebot
from telebot import types
from flask import Flask, request
from urllib.parse import urlparse

BOT_TOKEN = '8136008912:AAHwM1ZBZ2WxgCnFpRA0MC_EIr9KcRQiF3c'
WEBHOOK_BASE = 'https://download-bot-5sv5.onrender.com'

# -------------------- FULL YOUTUBE COOKIES (user-provided) --------------------
YT_COOKIES = """GPS=1;

__Secure-ROLLOUT_TOKEN=CO3i6YPBtsanChCbvJm5mJSPAxjQlq68xtqPAw%3D%3D;

VISITOR_INFO1_LIVE=rEI5E-U0Jx0;

VISITOR_PRIVACY_METADATA=CgJTTxIEGgAgPw%3D%3D;

LOGIN_INFO=AFmmF2swRAIgThQhY_6e8JjKt2uus66z8sdorQ3lx-bGhfpWftKI3skCIDnHwhEtf06N47YkCtPiF3H3RAPuoPkYqXsdO6J1ZiS7:QUQ3MjNmemJrYnYzYWJUZEhFQlVNNHZoQUROTDUxSVRST3NLWHhvNFdsLWFMbGs2c0ktdm9wY041NEczUWhXdlc5ZFZWNHd2NzdJS2wyM2RWUXEzOFV1WE9xSGZVQ3BqbnN2RWEtM1Btekt3cVJxZlUzXzJIX3UzTWI4WnZabDdELTVwMUw2ay1tbWlwNWJMZEhzX29Qcm93ZlFBVFg3aXR3;

PREF=f6=40000000&tz=Africa.Mogadishu&f7=100;

YSC=weUZegXyp0c;
"""
# ------------------------------------------------------------------------------

# Instagram cookies variable (kept if you later want to set IG_COOKIES)
IG_COOKIES = None

POSSIBLE_FFMPEG_PATHS = ["./ffmpeg", "/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg", "ffmpeg"]
DOWNLOAD_DIR = 'downloads'
MAX_TELEGRAM_FILESIZE = 2 * 1024 * 1024 * 1024

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
    logging.warning("ffmpeg binary not found in configured POSSIBLE_FFMPEG_PATHS. Place ffmpeg in one of those paths or adjust POSSIBLE_FFMPEG_PATHS.")
else:
    logging.info(f"ffmpeg found at: {FFMPEG_BINARY}")

if not BOT_TOKEN or BOT_TOKEN.startswith('<') or BOT_TOKEN.strip() == '':
    raise ValueError("BOT_TOKEN is not set. Open this file and set BOT_TOKEN = '<your token>' near the top of the file.")

WEBHOOK_URL = WEBHOOK_BASE.rstrip('/') + '/' + BOT_TOKEN

app = Flask(__name__)
bot = telebot.TeleBot(BOT_TOKEN)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log')
    ]
)
logger = logging.getLogger(__name__)

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

DOWNLOAD_SESSIONS = {}

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
]

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
        return matches[0]
    return None

def setup_instagram_cookies():
    """Setup Instagram cookies from IG_COOKIES variable if available (writes cookies to ig_cookies.txt)"""
    if IG_COOKIES:
        cookie_file = 'ig_cookies.txt'
        with open(cookie_file, 'w') as f:
            f.write(IG_COOKIES)
        return cookie_file
    return None

def setup_youtube_cookies():
    """
    Parse raw YT_COOKIES (a "name=value; name2=value2; ..." string)
    and write a Netscape-format cookie file (yt_cookies.txt) for yt-dlp to use.
    """
    if not YT_COOKIES:
        return None

    cookie_file = 'yt_cookies.txt'
    try:
        # Parse cookie string into dict
        cookies = {}
        # Split on semicolon, handle newlines/extra spaces
        parts = [p.strip() for p in YT_COOKIES.replace('\n',';').split(';') if p.strip()]
        for part in parts:
            if '=' in part:
                name, value = part.split('=', 1)
                cookies[name.strip()] = value.strip()

        # Write Netscape cookie file header
        # Format: domain\tflag\tpath\tsecure\texpiration\tname\tvalue
        with open(cookie_file, 'w', encoding='utf-8') as f:
            f.write('# Netscape HTTP Cookie File\n')
            # Put cookies for .youtube.com and .google.com to improve compatibility
            for name, value in cookies.items():
                for domain in ('.youtube.com', '.google.com'):
                    flag = 'TRUE'
                    path = '/'
                    secure = 'TRUE' if name.startswith('__Secure-') else 'FALSE'
                    expiration = '2147483647'  # far-future expiry
                    safe_name = name.replace('\t', '%09').replace('\n', '')
                    safe_value = value.replace('\t', '%09').replace('\n', '')
                    f.write(f"{domain}\t{flag}\t{path}\t{secure}\t{expiration}\t{safe_name}\t{safe_value}\n")
        return cookie_file
    except Exception as e:
        logger.exception(f"Failed to write YouTube cookie file: {e}")
        return None

def get_ydl_opts(for_download=False, url=None):
    """Get enhanced yt-dlp options with anti-bot detection measures"""
    base_opts = {
        'quiet': True,
        'no_warnings': True,
        'ffmpeg_location': FFMPEG_BINARY if FFMPEG_BINARY else None,
        'noplaylist': True,
        'extract_flat': False,
        'user_agent': random.choice(USER_AGENTS),
        'sleep_interval': 1,
        'max_sleep_interval': 3,
        'sleep_interval_requests': 1,
        'sleep_interval_subtitles': 1,
        'age_limit': 99,
        'socket_timeout': 30,
        'retries': 3,
        'fragment_retries': 5,
        'retry_sleep_functions': {
            'http': lambda n: 2 ** n,
            'fragment': lambda n: 2 ** n,
            'extractor': lambda n: 2 ** n,
        },
        'merge_output_format': 'mp4',
        'format_sort': ['res', 'codec:avc1:m4a', 'ext:mp4:m4a'],
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'tv'],
                'skip': ['hls', 'dash']
            }
        },
        'http_headers': {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-us,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
    }
    
    # Instagram: keep previous behavior
    if url and ('instagram.com' in url or 'instagr.am' in url):
        base_opts['http_headers']['Referer'] = 'https://www.instagram.com/'
        cookie_file = setup_instagram_cookies()
        if cookie_file:
            base_opts['cookiefile'] = cookie_file

    # YouTube: use cookiefile if provided to help with auth/blocks
    if url and ('youtube.com' in url or 'youtu.be' in url):
        # If the user provided a YT cookie string, write cookie file and attach it
        cookie_file = setup_youtube_cookies()
        if cookie_file:
            base_opts['cookiefile'] = cookie_file
        # Helpful headers for YouTube requests
        base_opts['http_headers'].update({
            'Referer': 'https://www.youtube.com/',
        })
        base_opts.setdefault('extractor_args', {}).setdefault('youtube', {}).update({
            'skip': ['hls']
        })
    
    if FFMPEG_BINARY:
        base_opts['postprocessor_args'] = ['-movflags', '+faststart']
    
    if not for_download:
        base_opts['skip_download'] = True
    else:
        base_opts['outtmpl'] = os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s')
    
    return base_opts

def extract_video_info(url):
    """Extract video information with retry mechanism"""
    for attempt in range(3):
        try:
            ydl_opts = get_ydl_opts(for_download=False, url=url)
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                return info
        except yt_dlp.utils.ExtractorError as e:
            logger.warning(f"Extractor error on attempt {attempt + 1}: {e}")
            if "Sign in to confirm your age" in str(e):
                logger.error("Age-restricted content - cannot access")
                return None
            if "private" in str(e).lower() or "not available" in str(e).lower():
                logger.error("Content is private or not available")
                return None
            if attempt == 2:
                logger.error(f"Failed to extract info after 3 attempts: {e}")
                return None
            time.sleep(2 ** attempt)
        except Exception as e:
            logger.exception(f"Error extracting video info on attempt {attempt + 1}: {e}")
            if attempt == 2:
                return None
            time.sleep(2 ** attempt)

def download_with_quality(url, quality_key):
    """Download video with enhanced error handling and retry mechanism"""
    create_downloads_folder()
    
    for attempt in range(3):
        try:
            ydl_opts = get_ydl_opts(for_download=True, url=url)
            
            if quality_key == 'audio':
                ydl_opts['format'] = "bestaudio/best"
                ydl_opts['postprocessors'] = [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }]
            else:
                height = int(quality_key)
                if FFMPEG_BINARY:
                    ydl_opts['format'] = f"bestvideo[height<={height}][vcodec*=avc1]+bestaudio[acodec*=mp4a]/bestvideo[height<={height}]+bestaudio/best[height<={height}][acodec!=none]"
                else:
                    ydl_opts['format'] = f"best[height<={height}][acodec!=none][vcodec*=avc1]/best[height<={height}][acodec!=none]/best[acodec!=none]"
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                
                vid_id = info.get('id') if isinstance(info, dict) else None
                if not vid_id and isinstance(info, dict) and 'entries' in info:
                    entries = info.get('entries')
                    if entries:
                        vid_id = entries[0].get('id')
                
                if vid_id:
                    path = find_downloaded_file(vid_id)
                    if path and os.path.exists(path):
                        return path
                
                filename = ydl.prepare_filename(info) if info else None
                if filename and os.path.exists(filename):
                    return filename
                
                files = glob.glob(os.path.join(DOWNLOAD_DIR, "*"))
                if files:
                    latest = max(files, key=os.path.getctime)
                    return latest
                    
                return None
                
        except yt_dlp.utils.ExtractorError as e:
            logger.warning(f"Extractor error on download attempt {attempt + 1}: {e}")
            if "private" in str(e).lower() or "not available" in str(e).lower():
                logger.error("Video is private or not available")
                return None
            if "login" in str(e).lower() or "sign in" in str(e).lower():
                logger.error("Content requires authentication")
                return None
            if attempt == 2:
                logger.error(f"Failed to download after 3 attempts: {e}")
                return None
            time.sleep(2 ** attempt)
        except Exception as e:
            logger.exception(f"Error downloading video on attempt {attempt + 1}: {e}")
            if attempt == 2:
                return None
            time.sleep(2 ** attempt)

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    try:
        username = bot.get_me().username
    except Exception:
        username = "bot"
    welcome_text = f"""
🌟 *Welcome to @{username}* 🌟

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
            bot.edit_message_text("❌ Failed to get video information. The link might be invalid, private, or age-restricted.",
                                  chat_id=message.chat.id,
                                  message_id=processing_msg.message_id)
            return

        title = clean_filename(info.get('title', 'Untitled'))
        duration = info.get('duration', 0)
        uploader = info.get('uploader', 'Unknown')
        vid_id = info.get('id', str(uuid.uuid4())[:8])

        session_id = uuid.uuid4().hex[:12]
        DOWNLOAD_SESSIONS[session_id] = {
            'url': text,
            'title': title,
            'id': vid_id,
            'chat_id': message.chat.id,
            'message_id': processing_msg.message_id
        }

        duration_str = f"{duration} seconds" if duration else "Unknown"
        details_text = f"""📹 *Video Details*:
- *Title:* {title}
- *Duration:* {duration_str}
- *Uploader:* {uploader}

Choose quality to download:
"""
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
        logger.exception(f"Error handling message: {e}")
        bot.edit_message_text("❌ An error occurred while processing your request. Please try again later.",
                              chat_id=message.chat.id,
                              message_id=processing_msg.message_id)

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    try:
        data = call.data
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

        bot.answer_callback_query(call.id, f"Selected: {quality_key}")
        bot.edit_message_text(f"⬇️ Downloading *{title}* — *{quality_key}* ...",
                              chat_id=chat_id,
                              message_id=orig_msg_id,
                              parse_mode='Markdown')

        start_ts = time.time()
        filepath = download_with_quality(url, quality_key)
        elapsed = time.time() - start_ts
        
        if not filepath or not os.path.exists(filepath):
            bot.edit_message_text("❌ Failed to download the file. It might be private, age-restricted, or temporarily unavailable.",
                                  chat_id=chat_id,
                                  message_id=orig_msg_id)
            DOWNLOAD_SESSIONS.pop(session_id, None)
            return

        filesize = os.path.getsize(filepath)
        human_mb = round(filesize / (1024*1024), 2)
        if filesize >= MAX_TELEGRAM_FILESIZE:
            bot.edit_message_text(f"⚠️ Download succeeded but file is too large to send via Telegram ({human_mb} MB).",
                                  chat_id=chat_id,
                                  message_id=orig_msg_id)
            DOWNLOAD_SESSIONS.pop(session_id, None)
            return

        try:
            username = bot.get_me().username
        except Exception:
            username = "bot"
        caption = f"🎥 *{title}*\n✅ Downloaded by @{username}"
        ext = os.path.splitext(filepath)[1].lower()
        try:
            with open(filepath, 'rb') as f:
                if quality_key == 'audio' or ext in ['.mp3', '.m4a', '.aac', '.ogg', '.opus']:
                    bot.send_audio(chat_id, f, caption=caption, parse_mode='Markdown', 
                                 reply_to_message_id=call.message.reply_to_message.message_id if call.message.reply_to_message else None)
                else:
                    bot.send_video(chat_id, f, caption=caption, parse_mode='Markdown', 
                                 reply_to_message_id=call.message.reply_to_message.message_id if call.message.reply_to_message else None)
        except Exception as e:
            logger.exception(f"Error sending file: {e}")
            bot.edit_message_text("❌ Downloaded but failed to send file via Telegram.", 
                                chat_id=chat_id, message_id=orig_msg_id)
            DOWNLOAD_SESSIONS.pop(session_id, None)
            return
            
        try:
            bot.delete_message(chat_id, orig_msg_id)
        except Exception:
            pass

        try:
            os.remove(filepath)
        except Exception:
            pass

        DOWNLOAD_SESSIONS.pop(session_id, None)

    except Exception as e:
        logger.exception(f"Callback handler error: {e}")
        try:
            bot.answer_callback_query(call.id, "An error occurred.")
        except:
            pass

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
        logger.exception(f"Webhook Error: {e}")
        return f"Webhook Error: {e}", 500
    return '', 200

if __name__ == "__main__":
    create_downloads_folder()
    try:
        bot.remove_webhook()
        time.sleep(0.5)
    except Exception:
        pass
    bot.set_webhook(url=WEBHOOK_URL)
    masked_url = WEBHOOK_URL.replace(BOT_TOKEN, "***TOKEN***")
    logger.info(f"✅ Webhook set to: {masked_url}")
    
    app.run(host="0.0.0.0", port=5000)
