import os
import logging
import requests
import telebot
import json
from flask import Flask, request, abort, render_template_string, jsonify
from datetime import datetime
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
import threading
import time
import io
from pymongo import MongoClient
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

BOT_TOKENS = [
    "7770743573:AAHcd9sypqLDkExcfFSEeIHC88QdiPEb1QM",
    "7790991731:AAF4NHGm0BJCf08JTdBaUWKzwfs82_Y9Ecw",
]

ASSEMBLYAI_API_KEY = "f692ac671b6e4d388e53f445f0d7d686"
OPENROUTER_API_KEY = "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"
WEBHOOK_BASE = "https://media-to-text-bot-m22d.onrender.com"
WEBHOOK_URL = WEBHOOK_BASE.rstrip("/")
ADMIN_ID = 6964068910
SECRET_KEY = "super-secret-please-change"
TELEGRAM_MAX_BYTES = 20 * 1024 * 1024
MONGO_URI = "mongodb+srv://hoskasii:GHyCdwpI0PvNuLTg@cluster0.dy7oe7t.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"
DB_NAME = "telegram_bot_db"
MAX_WEB_UPLOAD_MB = 250
REQUEST_TIMEOUT_ASSEMBLYAI = 300
REQUEST_TIMEOUT_TELEGRAM = 300
REQUEST_TIMEOUT_LLM = 300

client = MongoClient(MONGO_URI)
db = client[DB_NAME]
users_collection = db["users"]
groups_collection = db["groups"]

app = Flask(__name__)

bots = []
for token in BOT_TOKENS:
    bots.append(telebot.TeleBot(token, threaded=True, parse_mode='HTML'))

serializer = URLSafeTimedSerializer(SECRET_KEY)

LANG_OPTIONS = [
    ("🇬🇧 English", "en"),
    ("🇩🇪 Deutsch", "de"),
    ("🇮🇳 हिन्दी", "hi"),
    ("🇷🇺 Русский", "ru"),
    ("🇮🇷 فارسی", "fa"),
    ("🇮🇩 Indonesia", "id"),
    ("🇺🇦 Українська", "uk"),
    ("🇦🇿 Azərbaycan", "az"),
    ("🇮🇹 Italiano", "it"),
    ("🇹🇷 Türkçe", "tr"),
    ("🇧🇬 Български", "bg"),
    ("🇷🇸 Srpski", "sr"),
    ("🇫🇷 Français", "fr"),
    ("🇸🇦 العربية", "ar"),
    ("🇪🇸 Español", "es"),
    ("🇵🇰 اردو", "ur"),
    ("🇹🇭 ไทย", "th"),
    ("🇻🇳 Tiếng Việt", "vi"),
    ("🇯🇵 日本語", "ja"),
    ("🇰🇷 한국어", "ko"),
    ("🇨🇳 中文", "zh"),
    ("🇳🇱 Nederlands", "nl"),
    ("🇸🇪 Svenska", "sv"),
    ("🇳🇴 Norsk", "no"),
    ("🇮🇱 עברית", "he"),
    ("🇩🇰 Dansk", "da"),
    ("🇪🇹 አማርኛ", "am"),
    ("🇫🇮 Suomi", "fi"),
    ("🇧🇩 বাংলা", "bn"),
    ("🇰🇪 Kiswahili", "sw"),
    ("🇪🇹 Oromo", "om"),
    ("🇳🇵 नेपाली", "ne"),
    ("🇵🇱 Polski", "pl"),
    ("🇬🇷 Ελληνικά", "el"),
    ("🇨🇿 Čeština", "cs"),
    ("🇭🇺 Magyar", "hu"),
    ("🇷🇴 Română", "ro"),
    ("🇲🇾 Melayu", "ms"),
    ("🇺🇿 O'zbekcha", "uz"),
    ("🇵🇭 Tagalog", "tl"),
    ("🇵🇹 Português", "pt")
]

CODE_TO_LABEL = {code: label for (label, code) in LANG_OPTIONS}
LABEL_TO_CODE = {label: code for (label, code) in LANG_OPTIONS}

STT_LANGUAGES = {}
for label, code in LANG_OPTIONS:
    STT_LANGUAGES[label.split(" ", 1)[-1]] = {
        "code": code,
        "emoji": label.split(" ", 1)[0],
        "native": label.split(" ", 1)[-1]
    }

user_transcriptions = {}
memory_lock = threading.Lock()
in_memory_data = {"pending_media": {}}
admin_broadcast_state = {}

ALLOWED_EXTENSIONS = {
    "mp3", "wav", "m4a", "ogg", "webm", "flac", "mp4", "mkv", "avi", "mov", "hevc", "aac", "aiff", "amr", "wma", "opus", "m4v", "ts", "flv", "3gp"
}

def update_user_activity(user_id: int):
    user_id_str = str(user_id)
    now = datetime.now()
    users_collection.update_one(
        {"_id": user_id_str},
        {"$set": {"last_active": now}, "$setOnInsert": {"first_seen": now, "stt_conversion_count": 0}},
        upsert=True
    )

def increment_processing_count(user_id: str, service_type: str):
    field_to_inc = f"{service_type}_conversion_count"
    users_collection.update_one(
        {"_id": str(user_id)},
        {"$inc": {field_to_inc: 1}}
    )

def get_stt_user_lang(user_id: str) -> str:
    user_data = users_collection.find_one({"_id": user_id})
    if user_data and "stt_language" in user_data:
        return user_data["stt_language"]
    return "en"

def set_stt_user_lang(user_id: str, lang_code: str):
    users_collection.update_one(
        {"_id": user_id},
        {"$set": {"stt_language": lang_code}},
        upsert=True
    )

def user_has_stt_setting(user_id: str) -> bool:
    user_data = users_collection.find_one({"_id": user_id})
    return user_data is not None and "stt_language" in user_data

def save_pending_media(user_id: str, media_type: str, data: dict):
    with memory_lock:
        in_memory_data["pending_media"][user_id] = {
            "media_type": media_type,
            "data": data,
            "saved_at": datetime.now()
        }

def pop_pending_media(user_id: str):
    with memory_lock:
        return in_memory_data["pending_media"].pop(user_id, None)

def delete_transcription_later(user_id: str, message_id: int):
    time.sleep(600)
    with memory_lock:
        if user_id in user_transcriptions and message_id in user_transcriptions[user_id]:
            del user_transcriptions[user_id][message_id]

def assemblyai_upload_from_stream(stream_iterable):
    upload_url = "https://api.assemblyai.com/v2/upload"
    headers = {"authorization": ASSEMBLYAI_API_KEY}
    resp = requests.post(upload_url, headers=headers, data=stream_iterable, timeout=3600)
    resp.raise_for_status()
    return resp.json().get("upload_url")

def select_speech_model_for_lang(language_code: str):
    return "best"

def create_transcript_and_wait(audio_url: str, language_code: str = None, speech_model: str = None, poll_interval=2):
    create_url = "https://api.assemblyai.com/v2/transcript"
    headers = {"authorization": ASSEMBLYAI_API_KEY, "content-type": "application/json"}
    data = {"audio_url": audio_url}
    if language_code:
        data["language_code"] = language_code
    if speech_model:
        data["speech_model"] = speech_model
    resp = requests.post(create_url, headers=headers, json=data, timeout=REQUEST_TIMEOUT_ASSEMBLYAI)
    resp.raise_for_status()
    job = resp.json()
    job_id = job.get("id")
    get_url = f"{create_url}/{job_id}"
    while True:
        r = requests.get(get_url, headers={"authorization": ASSEMBLYAI_API_KEY}, timeout=REQUEST_TIMEOUT_ASSEMBLYAI)
        r.raise_for_status()
        status = r.json()
        st = status.get("status")
        if st == "completed":
            return status.get("text", "")
        if st == "failed":
            raise RuntimeError("Transcription failed: " + str(status.get("error", "unknown error")))
        time.sleep(poll_interval)

def telegram_file_stream(file_url, chunk_size=256*1024):
    with requests.get(file_url, stream=True, timeout=REQUEST_TIMEOUT_TELEGRAM) as r:
        r.raise_for_status()
        for chunk in r.iter_content(chunk_size=chunk_size):
            if chunk:
                yield chunk

def telegram_file_info_and_url(bot_token: str, file_id):
    import urllib.request
    url = f"https://api.telegram.org/bot{bot_token}/getFile?file_id={file_id}"
    resp = requests.get(url, timeout=REQUEST_TIMEOUT_TELEGRAM)
    resp.raise_for_status()
    j = resp.json()
    file_path = j.get("result", {}).get("file_path")
    file_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
    class Dummy:
        pass
    d = Dummy()
    d.file_path = file_path
    return d, file_url

def is_transcoding_like_error(msg: str) -> bool:
    if not msg:
        return False
    m = msg.lower()
    checks = [
        "transcoding failed",
        "file does not appear to contain audio",
        "text/html",
        "html document",
        "unsupported media type",
        "could not decode",
    ]
    return any(ch in m for ch in checks)

def build_lang_keyboard(callback_prefix: str, row_width: int = 3, message_id: int = None):
    markup = InlineKeyboardMarkup(row_width=row_width)
    buttons = []
    for label, code in LANG_OPTIONS:
        if message_id is not None:
            cb = f"{callback_prefix}|{code}|{message_id}"
        else:
            cb = f"{callback_prefix}|{code}"
        buttons.append(InlineKeyboardButton(label, callback_data=cb))
    for i in range(0, len(buttons), row_width):
        markup.add(*buttons[i:i+row_width])
    return markup

def build_admin_keyboard():
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("Send Broadcast", callback_data="admin_send_broadcast"))
    markup.add(InlineKeyboardButton("Total Users", callback_data="admin_total_users"))
    return markup

def signed_upload_token(chat_id: int, lang_code: str):
    payload = {"chat_id": chat_id, "lang": lang_code}
    return serializer.dumps(payload)

def unsign_upload_token(token: str, max_age_seconds: int = 3600):
    data = serializer.loads(token, max_age=max_age_seconds)
    return data

def animate_processing_message(bot_obj, chat_id, message_id, stop_event):
    dots = [".", "..", "..."]
    idx = 0
    while not stop_event():
        try:
            bot_obj.edit_message_text(f"🔄 Processing{dots[idx % len(dots)]}", chat_id=chat_id, message_id=message_id)
        except Exception:
            pass
        idx = (idx + 1) % len(dots)
        time.sleep(0.6)

def ask_deepseek_r1(prompt: str, timeout=REQUEST_TIMEOUT_LLM) -> str:
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "deepseek/deepseek-r1:free",
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 800
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    resp.raise_for_status()
    j = resp.json()
    if "choices" in j and len(j["choices"]) > 0:
        choice = j["choices"][0]
        if isinstance(choice.get("message"), dict):
            return choice["message"].get("content", "").strip()
        return choice.get("text", "").strip()
    if "data" in j and isinstance(j["data"], list) and len(j["data"]) > 0:
        return j["data"][0].get("text", "").strip()
    return ""

def safe_extension_from_filename(filename: str):
    if not filename or "." not in filename:
        return ""
    return filename.rsplit(".", 1)[-1].lower()

def handle_media_common(message, bot_obj, bot_token):
    user_id_str = str(message.from_user.id)
    chat_id_str = str(message.chat.id)
    update_user_activity(message.from_user.id)
    file_id = None
    file_size = None
    filename = None
    if message.voice:
        file_id = message.voice.file_id
        file_size = message.voice.file_size
        filename = "voice.ogg"
    elif message.audio:
        file_id = message.audio.file_id
        file_size = message.audio.file_size
        filename = getattr(message.audio, "file_name", "audio")
    elif message.video:
        file_id = message.video.file_id
        file_size = message.video.file_size
        filename = getattr(message.video, "file_name", "video.mp4")
    elif message.document:
        mime = getattr(message.document, "mime_type", None)
        filename = getattr(message.document, "file_name", None) or "file"
        ext = safe_extension_from_filename(filename)
        if mime and ("audio" in mime or "video" in mime):
            file_id = message.document.file_id
            file_size = message.document.file_size
        elif ext in ALLOWED_EXTENSIONS:
            file_id = message.document.file_id
            file_size = message.document.file_size
        else:
            bot_obj.send_message(message.chat.id, "Sorry, I can only transcribe audio or video files.")
            return
    lang = get_stt_user_lang(user_id_str)
    if file_size and file_size > TELEGRAM_MAX_BYTES:
        token = signed_upload_token(message.chat.id, lang)
        upload_link = f"{WEBHOOK_BASE}/upload/{token}"
        pretty_size_mb = round(file_size / (1024*1024), 2)
        text = (
            "📁 <b>File Too Large for Telegram</b>\n"
            f"Your file is {pretty_size_mb}MB, which exceeds Telegram's 20MB limit.\n\n"
            "🌐 <b>Upload via Web Interface:</b>\n"
            "👆 Click the link below to upload your large file:\n\n"
            f"🔗 <a href=\"{upload_link}\">Upload Large File</a>\n\n"
            f"✅ Your language preference ({lang}) is already set!\n"
            "Link expires in 1 hour."
        )
        bot_obj.send_message(message.chat.id, text, disable_web_page_preview=True, reply_to_message_id=message.message_id)
        return
    processing_msg = bot_obj.send_message(message.chat.id, "🔄 Processing", reply_to_message_id=message.message_id)
    processing_msg_id = processing_msg.message_id
    stop_animation = {"stop": False}
    def stop_event():
        return stop_animation["stop"]
    animation_thread = threading.Thread(target=animate_processing_message, args=(bot_obj, message.chat.id, processing_msg_id, stop_event))
    animation_thread.start()
    try:
        tf, file_url = telegram_file_info_and_url(bot_token, file_id)
        gen = telegram_file_stream(file_url)
        upload_url = assemblyai_upload_from_stream(gen)
        speech_model = select_speech_model_for_lang(lang)
        text = create_transcript_and_wait(upload_url, language_code=lang, speech_model=speech_model)
        if len(text) > 4000:
            f = io.BytesIO(text.encode("utf-8"))
            f.name = "transcription.txt"
            markup = InlineKeyboardMarkup()
            sent = bot_obj.send_document(message.chat.id, f, caption="Transcription too long. Here's the complete text in a file.", reply_to_message_id=message.message_id, reply_markup=markup)
            try:
                btn = InlineKeyboardButton("Get Key Points", callback_data=f"get_key_points|{message.chat.id}|{sent.message_id}")
                markup.add(btn)
                bot_obj.edit_message_reply_markup(message.chat.id, sent.message_id, reply_markup=markup)
            except Exception:
                pass
            try:
                uid_key = str(message.chat.id)
                user_transcriptions.setdefault(uid_key, {})[sent.message_id] = text
                threading.Thread(target=delete_transcription_later, args=(uid_key, sent.message_id), daemon=True).start()
            except Exception:
                pass
        elif len(text) > 800:
            truncated = text[:800].rstrip()
            if len(truncated) < len(text):
                truncated = truncated + "..."
            markup = InlineKeyboardMarkup()
            sent_msg = bot_obj.send_message(message.chat.id, truncated or "No transcription text was returned.", reply_to_message_id=message.message_id, reply_markup=markup)
            try:
                btn = InlineKeyboardButton("Get Summarize", callback_data=f"get_key_points|{message.chat.id}|{sent_msg.message_id}")
                markup.add(btn)
                bot_obj.edit_message_reply_markup(message.chat.id, sent_msg.message_id, reply_markup=markup)
            except Exception:
                pass
            try:
                uid_key = str(message.chat.id)
                user_transcriptions.setdefault(uid_key, {})[sent_msg.message_id] = text
                threading.Thread(target=delete_transcription_later, args=(uid_key, sent_msg.message_id), daemon=True).start()
            except Exception:
                pass
        else:
            markup = InlineKeyboardMarkup()
            sent_msg = bot_obj.send_message(message.chat.id, text or "No transcription text was returned.", reply_to_message_id=message.message_id, reply_markup=markup)
            try:
                uid_key = str(message.chat.id)
                user_transcriptions.setdefault(uid_key, {})[sent_msg.message_id] = text
                threading.Thread(target=delete_transcription_later, args=(uid_key, sent_msg.message_id), daemon=True).start()
            except Exception:
                pass
        increment_processing_count(user_id_str, "stt")
    except Exception as e:
        error_msg = str(e)
        logging.exception("Error in transcription process")
        if is_transcoding_like_error(error_msg):
            bot_obj.send_message(message.chat.id, "⚠️ Transcription error: file is not audible. Please send a different file.", reply_to_message_id=message.message_id)
        else:
            bot_obj.send_message(message.chat.id, f"Error during transcription: {error_msg}", reply_to_message_id=message.message_id)
    finally:
        stop_animation["stop"] = True
        animation_thread.join()
        try:
            bot_obj.delete_message(message.chat.id, processing_msg_id)
        except Exception:
            pass

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
    <title>Media to Text Bot</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"/>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet"/>
    <style>
        :root {
            --primary: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            --success: linear-gradient(135deg, #10b981, #059669);
            --danger: linear-gradient(135deg, #ef4444, #dc2626);
            --card-bg: rgba(255, 255, 255, 0.95);
            --shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.25);
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            background: var(--primary);
            min-height: 100vh;
            overflow-x: hidden;
        }
        .app-container {
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        .main-card {
            background: var(--card-bg);
            backdrop-filter: blur(20px);
            border-radius: 20px;
            box-shadow: var(--shadow);
            border: 1px solid rgba(255, 255, 255, 0.2);
            max-width: 600px;
            width: 100%;
            overflow: hidden;
            transition: all 0.3s ease;
        }
        .main-card:hover {
            transform: translateY(-5px);
            box-shadow: 0 32px 64px -12px rgba(0, 0, 0, 0.3);
        }
        .header {
            background: var(--primary);
            color: white;
            padding: 2.5rem 2rem;
            text-align: center;
            position: relative;
            overflow: hidden;
        }
        .header h1 {
            font-size: 2rem;
            font-weight: 700;
            margin-bottom: 0.5rem;
            text-shadow: 0 2px 4px rgba(0,0,0,0.3);
        }
        .header p {
            opacity: 0.9;
            font-size: 1.1rem;
        }
        .card-body { padding: 2.5rem; }
        .form-group { margin-bottom: 2rem; }
        .form-label {
            font-weight: 600;
            color: #374151;
            margin-bottom: 0.8rem;
            display: flex;
            align-items: center;
            gap: 0.5rem;
            font-size: 1.1rem;
        }
        .form-select, .form-control {
            border: 2px solid #e5e7eb;
            border-radius: 15px;
            padding: 1rem 1.2rem;
            font-size: 1rem;
            transition: all 0.3s ease;
            background: white;
            box-shadow: 0 2px 4px rgba(0,0,0,0.05);
        }
        .form-select:focus, .form-control:focus {
            border-color: #667eea;
            box-shadow: 0 0 0 4px rgba(102, 126, 234, 0.1);
            outline: none;
        }
        .upload-area {
            border: 3px dashed #d1d5db;
            border-radius: 20px;
            padding: 3rem 2rem;
            text-align: center;
            transition: all 0.3s ease;
            cursor: pointer;
            background: #f8fafc;
            position: relative;
        }
        .upload-area:hover {
            border-color: #667eea;
            background: #f0f9ff;
            transform: scale(1.02);
        }
        .upload-area.dragover {
            border-color: #667eea;
            background: #667eea;
            color: white;
        }
        .upload-icon {
            font-size: 4rem;
            color: #667eea;
            margin-bottom: 1.5rem;
            transition: all 0.3s ease;
        }
        .dragover .upload-icon { color: white; transform: scale(1.2); }
        .upload-text {
            font-size: 1.3rem;
            font-weight: 600;
            color: #374151;
            margin-bottom: 0.8rem;
        }
        .dragover .upload-text { color: white; }
        .upload-hint {
            color: #6b7280;
            font-size: 1rem;
        }
        .dragover .upload-hint { color: rgba(255, 255, 255, 0.9); }
        .btn-primary {
            background: var(--primary);
            border: none;
            border-radius: 15px;
            padding: 1rem 2.5rem;
            font-weight: 600;
            font-size: 1.1rem;
            transition: all 0.3s ease;
            position: relative;
            overflow: hidden;
        }
        .btn-primary:hover {
            transform: translateY(-3px);
            box-shadow: 0 15px 35px -5px rgba(102, 126, 234, 0.4);
        }
        .status-message {
            padding: 1.5rem;
            border-radius: 15px;
            margin: 2rem 0;
            font-weight: 500;
            display: flex;
            align-items: center;
            gap: 1rem;
            font-size: 1.1rem;
        }
        .status-processing {
            background: linear-gradient(135deg, #3b82f6, #1d4ed8);
            color: white;
        }
        .status-success {
            background: var(--success);
            color: white;
        }
        .status-error {
            background: var(--danger);
            color: white;
        }
        .result-container {
            background: #f8fafc;
            border: 1px solid #e2e8f0;
            border-radius: 15px;
            padding: 2rem;
            margin-top: 2rem;
        }
        .result-text {
            font-family: 'Georgia', serif;
            line-height: 1.8;
            color: #1f2937;
            font-size: 1.1rem;
        }
        .close-notice {
            background: linear-gradient(135deg, #10b981, #059669);
            color: white;
            padding: 1.5rem;
            border-radius: 15px;
            margin: 2rem 0;
            text-align: center;
            font-weight: 600;
            font-size: 1.1rem;
        }
        .progress-wrap { margin-top: 1rem; text-align: left; }
        .progress-bar-outer {
            width: 100%;
            background: #e6eefc;
            border-radius: 12px;
            overflow: hidden;
            height: 18px;
        }
        .progress-bar-inner {
            height: 100%;
            width: 0%;
            background: linear-gradient(90deg,#6ee7b7,#3b82f6);
            transition: width 0.2s ease;
        }
        .bytes-info {
            margin-top: 0.5rem;
            font-size: 0.95rem;
            color: #374151;
        }
        @keyframes pulse {
            0%, 100% { transform: scale(1); }
            50% { transform: scale(1.1); }
        }
        .pulse-icon { animation: pulse 2s infinite; }
        .hidden { display: none !important; }
        @media (max-width: 768px) {
            .app-container { padding: 15px; }
            .main-card { margin: 0; }
            .header h1 { font-size: 1.8rem; }
            .card-body { padding: 2rem; }
            .upload-area { padding: 2.5rem 1.5rem; }
            .upload-icon { font-size: 3rem; }
        }
    </style>
</head>
<body>
    <div class="app-container">
        <div class="main-card">
            <div class="header">
                <h1><i class="fas fa-microphone-alt"></i> Media to Text Bot</h1>
                <p>Transform your media files into accurate text</p>
            </div>
            <div class="card-body">
                <form id="transcriptionForm" enctype="multipart/form-data" method="post">
                    <div class="form-group">
                        <label class="form-label" for="language">
                            <i class="fas fa-globe-americas"></i> Language
                        </label>
                        <select class="form-select" id="language" name="language" required>
                            {% for label, code in lang_options %}
                            <option value="{{ code }}" {% if code == selected_lang %}selected{% endif %}>{{ label }}</option>
                            {% endfor %}
                        </select>
                    </div>
                    <div class="form-group">
                        <label class="form-label">
                            <i class="fas fa-file-audio"></i> Media File
                        </label>
                        <div class="upload-area" id="uploadArea">
                            <div class="upload-icon">
                                <i class="fas fa-cloud-upload-alt"></i>
                            </div>
                            <div class="upload-text">Drop your media here</div>
                            <div class="upload-hint">MP3, WAV, M4A, OGG, WEBM, FLAC, MP4, MKV, AVI, MOV, HEVC • Max {{ max_mb }}MB</div>
                            <input type="file" id="audioFile" name="file" accept=".mp3,.wav,.m4a,.ogg,.webm,.flac,.mp4,.mkv,.avi,.mov,.hevc,.aac,.aiff,.amr,.wma,.opus,.m4v,.ts,.flv,.3gp" class="d-none" required>
                        </div>
                    </div>
                    <button type="button" id="uploadButton" class="btn btn-primary w-100">
                        <i class="fas fa-magic"></i> Upload & Start
                    </button>
                </form>
                <div id="statusContainer"></div>
                <div id="resultContainer"></div>
            </div>
        </div>
    </div>
    <script>
        class TranscriptionApp {
            constructor() {
                this.initializeEventListeners();
            }
            initializeEventListeners() {
                this.uploadArea = document.getElementById('uploadArea');
                this.fileInput = document.getElementById('audioFile');
                this.uploadButton = document.getElementById('uploadButton');
                this.statusContainer = document.getElementById('statusContainer');
                this.resultContainer = document.getElementById('resultContainer');
                this.uploadArea.addEventListener('click', () => this.fileInput.click());
                this.fileInput.addEventListener('change', (e) => this.handleFileSelect(e));
                this.uploadArea.addEventListener('dragover', (e) => {
                    e.preventDefault();
                    this.uploadArea.classList.add('dragover');
                });
                this.uploadArea.addEventListener('dragleave', () => {
                    this.uploadArea.classList.remove('dragover');
                });
                this.uploadArea.addEventListener('drop', (e) => {
                    e.preventDefault();
                    this.uploadArea.classList.remove('dragover');
                    const files = e.dataTransfer.files;
                    if (files.length > 0) {
                        this.fileInput.files = files;
                        this.handleFileSelect({ target: this.fileInput });
                    }
                });
                this.uploadButton.addEventListener('click', (e) => this.handleSubmit(e));
            }
            humanFileSize(bytes) {
                if (bytes === 0) return '0 B';
                const k = 1024;
                const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
                const i = Math.floor(Math.log(bytes) / Math.log(k));
                return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
            }
            handleFileSelect(e) {
                const file = e.target.files[0];
                if (file) {
                    const uploadText = document.querySelector('.upload-text');
                    uploadText.textContent = `Selected: ${file.name} (${this.humanFileSize(file.size)})`;
                }
            }
            showUploadingUI() {
                this.statusContainer.innerHTML = `
                    <div class="status-message status-processing">
                        <i class="fas fa-spinner fa-spin pulse-icon"></i>
                        <div>
                            <div id="uploadStatusText">Upload Processing..</div>
                            <div class="progress-wrap">
                                <div class="progress-bar-outer"><div id="progressInner" class="progress-bar-inner"></div></div>
                                <div id="bytesInfo" class="bytes-info"></div>
                            </div>
                        </div>
                    </div>
                `;
            }
            async handleSubmit(e) {
                e.preventDefault();
                const file = this.fileInput.files[0];
                if (!file) {
                    alert("Please choose a file to upload.");
                    return;
                }
                if (file.size > {{ max_mb }} * 1024 * 1024) {
                    alert("File is too large. Max allowed is {{ max_mb }}MB.");
                    return;
                }
                const formData = new FormData();
                formData.append('file', file);
                formData.append('language', document.getElementById('language').value);
                this.showUploadingUI();
                const progressInner = document.getElementById('progressInner');
                const bytesInfo = document.getElementById('bytesInfo');
                const uploadStatusText = document.getElementById('uploadStatusText');
                const xhr = new XMLHttpRequest();
                xhr.open('POST', window.location.pathname, true);
                xhr.upload.onprogress = (event) => {
                    if (event.lengthComputable) {
                        const percent = Math.round((event.loaded / event.total) * 100);
                        progressInner.style.width = percent + '%';
                        bytesInfo.textContent = `${(event.loaded/1024/1024).toFixed(2)} MB / ${(event.total/1024/1024).toFixed(2)} MB (${percent}%)`;
                        uploadStatusText.textContent = `Uploading... ${percent}%`;
                    } else {
                        progressInner.style.width = '50%';
                        bytesInfo.textContent = `${(event.loaded/1024/1024).toFixed(2)} MB uploaded`;
                        uploadStatusText.textContent = `Uploading...`;
                    }
                };
                xhr.onload = () => {
                    if (xhr.status >= 200 && xhr.status < 300) {
                        let respText = "Upload accepted. Processing started. You may close this tab.";
                        try {
                            const j = JSON.parse(xhr.responseText);
                            if (j && j.message) respText = j.message;
                        } catch (err) {
                            respText = xhr.responseText || respText;
                        }
                        this.statusContainer.innerHTML = `
                            <div class="close-notice">
                                <i class="fas fa-check-circle"></i>
                                ${respText}
                            </div>
                        `;
                    } else {
                        let text = xhr.responseText || 'Upload failed';
                        this.statusContainer.innerHTML = `
                            <div class="status-message status-error">
                                <i class="fas fa-exclamation-triangle"></i>
                                <span>Upload failed. ${text}</span>
                            </div>
                        `;
                    }
                };
                xhr.onerror = () => {
                    this.statusContainer.innerHTML = `
                        <div class="status-message status-error">
                            <i class="fas fa-exclamation-triangle"></i>
                            <span>Upload failed. Please try again.</span>
                        </div>
                    `;
                };
                xhr.send(formData);
            }
        }
        document.addEventListener('DOMContentLoaded', () => {
            new TranscriptionApp();
        });
    </script>
</body>
</html>
"""

@app.route("/upload/<token>", methods=['GET', 'POST'])
def upload_large_file(token):
    try:
        data = unsign_upload_token(token, max_age_seconds=3600)
    except SignatureExpired:
        return "<h3>Link expired</h3>", 400
    except BadSignature:
        return "<h3>Invalid link</h3>", 400
    chat_id = data.get("chat_id")
    lang = data.get("lang", "en")
    if request.method == 'GET':
        return render_template_string(HTML_TEMPLATE, lang_options=LANG_OPTIONS, selected_lang=lang, max_mb=MAX_WEB_UPLOAD_MB)
    file = request.files.get('file')
    if not file:
        return "No file uploaded", 400
    file_bytes = file.read()
    if len(file_bytes) > MAX_WEB_UPLOAD_MB * 1024 * 1024:
        return f"File too large. Max allowed is {MAX_WEB_UPLOAD_MB}MB.", 400
    def bytes_gen(b):
        chunk_size = 256*1024
        bio = io.BytesIO(b)
        while True:
            chunk = bio.read(chunk_size)
            if not chunk:
                break
            yield chunk
    def process_uploaded_file(chat_id_inner, lang_inner, b):
        try:
            upload_url = assemblyai_upload_from_stream(bytes_gen(b))
            speech_model = select_speech_model_for_lang(lang_inner)
            text = create_transcript_and_wait(upload_url, language_code=lang_inner, speech_model=speech_model)
            sent_msg = None
            try:
                markup = InlineKeyboardMarkup()
                if len(text) > 4000:
                    fobj = io.BytesIO(text.encode("utf-8"))
                    fobj.name = "transcription.txt"
                    sent_msg = bots[0].send_document(chat_id_inner, fobj, caption="Transcription too long. Here's the complete text in a file.", reply_markup=markup)
                elif len(text) > 800:
                    truncated = text[:800].rstrip()
                    if len(truncated) < len(text):
                        truncated = truncated + "..."
                    sent_msg = bots[0].send_message(chat_id_inner, truncated or "No transcription text was returned.", reply_markup=markup)
                else:
                    sent_msg = bots[0].send_message(chat_id_inner, text or "No transcription text was returned.", reply_markup=markup)
                try:
                    if len(text) > 800:
                        btn = InlineKeyboardButton("Get Key Points", callback_data=f"get_key_points|{chat_id_inner}|{sent_msg.message_id}")
                        markup.add(btn)
                        bots[0].edit_message_reply_markup(chat_id_inner, sent_msg.message_id, reply_markup=markup)
                except Exception:
                    pass
            except Exception:
                try:
                    bots[0].send_message(chat_id_inner, "Error sending transcription message. The transcription completed but could not be delivered as a message.")
                except Exception:
                    pass
                return
            try:
                uid_key = str(chat_id_inner)
                user_transcriptions.setdefault(uid_key, {})[sent_msg.message_id] = text
                threading.Thread(target=delete_transcription_later, args=(uid_key, sent_msg.message_id), daemon=True).start()
                increment_processing_count(str(chat_id_inner), "stt")
            except Exception:
                pass
        except Exception:
            try:
                bots[0].send_message(chat_id_inner, "Error occurred while transcribing the uploaded file.")
            except Exception:
                pass
    threading.Thread(target=process_uploaded_file, args=(chat_id, lang, file_bytes), daemon=True).start()
    return jsonify({"status": "accepted", "message": "Upload accepted. Processing started. Your transcription will be sent to your Telegram chat when ready."})

def register_handlers(bot_obj, bot_token):
    @bot_obj.message_handler(commands=['start', 'admin'])
    def start_handler(message):
        try:
            chat_id = message.chat.id
            if chat_id == ADMIN_ID and message.text.lower() == '/admin':
                bot_obj.send_message(
                    chat_id,
                    "👋 Welcome, Admin! Choose an option:",
                    reply_markup=build_admin_keyboard()
                )
            else:
                update_user_activity(message.from_user.id)
                bot_obj.send_message(
                    message.chat.id,
                    "Choose your file language for transcription using the below buttons:",
                    reply_markup=build_lang_keyboard("start_select_lang")
                )
        except Exception:
            logging.exception("Error in start_handler")

    @bot_obj.callback_query_handler(func=lambda c: c.data and c.data.startswith("start_select_lang|"))
    def start_select_lang_callback(call):
        try:
            uid = str(call.from_user.id)
            _, lang_code = call.data.split("|", 1)
            lang_label = CODE_TO_LABEL.get(lang_code, lang_code)
            set_stt_user_lang(uid, lang_code)
            try:
                bot_obj.delete_message(call.message.chat.id, call.message.message_id)
            except Exception:
                pass
            welcome_text = (
                f"👋 Welcome! I am the best Media transcriber bot that is completely free!    \n"
                "• Send me\n"
                "• voice message\n"
                "• audio file\n"
                "• video\n"
                "• to transcribe for free"
            )
            bot_obj.send_message(call.message.chat.id, welcome_text)
            bot_obj.answer_callback_query(call.id, f"✅ Language set to {lang_label}")
        except Exception:
            logging.exception("Error in start_select_lang_callback")
            try:
                bot_obj.answer_callback_query(call.id, "❌ Error setting language", show_alert=True)
            except Exception:
                pass

    @bot_obj.message_handler(commands=['help'])
    def handle_help(message):
        try:
            update_user_activity(message.from_user.id)
            text = (
                "Commands supported:\n"
                "/start - Show welcome message\n"
                "/lang  - Change language\n"
                "/help  - This help message\n\n"
                "Send a voice/audio/video (up 20MB for Telegram) and I will transcribe it.\n"
                "If it's larger than Telegram limits, you'll be provided a secure web upload link (supports up to 250MB) Need more help? Contact: @boyso20"
            )
            bot_obj.send_message(message.chat.id, text)
        except Exception:
            logging.exception("Error in handle_help")

    @bot_obj.message_handler(commands=['lang'])
    def handle_lang(message):
        try:
            kb = build_lang_keyboard("stt_lang")
            bot_obj.send_message(message.chat.id, "Choose your file language for transcription using the below buttons:", reply_markup=kb)
        except Exception:
            logging.exception("Error in handle_lang")

    @bot_obj.callback_query_handler(lambda c: c.data and c.data.startswith("stt_lang|"))
    def on_stt_language_select(call):
        try:
            uid = str(call.from_user.id)
            _, lang_code = call.data.split("|", 1)
            lang_label = CODE_TO_LABEL.get(lang_code, lang_code)
            set_stt_user_lang(uid, lang_code)
            bot_obj.answer_callback_query(call.id, f"✅ Language set: {lang_label}")
            try:
                bot_obj.delete_message(call.message.chat.id, call.message.message_id)
            except Exception:
                pass
        except Exception:
            logging.exception("Error in on_stt_language_select")
            try:
                bot_obj.answer_callback_query(call.id, "❌ Error setting language", show_alert=True)
            except Exception:
                pass

    @bot_obj.message_handler(content_types=['new_chat_members'])
    def handle_new_chat_members(message):
        try:
            if message.new_chat_members[0].id == bot_obj.get_me().id:
                group_data = {
                    '_id': str(message.chat.id),
                    'title': message.chat.title,
                    'type': message.chat.type,
                    'added_date': datetime.now()
                }
                groups_collection.update_one({'_id': group_data['_id']}, {'$set': group_data}, upsert=True)
                bot_obj.send_message(message.chat.id, "Thanks for adding me! I'm ready to transcribe your media files.")
        except Exception:
            logging.exception("Error in handle_new_chat_members")

    @bot_obj.message_handler(content_types=['left_chat_member'])
    def handle_left_chat_member(message):
        try:
            if message.left_chat_member.id == bot_obj.get_me().id:
                groups_collection.delete_one({'_id': str(message.chat.id)})
        except Exception:
            logging.exception("Error in handle_left_chat_member")

    @bot_obj.message_handler(content_types=['voice', 'audio', 'video', 'document'])
    def handle_media_types(message):
        try:
            if message.chat.id == ADMIN_ID and admin_broadcast_state.get(message.chat.id, False):
                bot_obj.send_message(message.chat.id, "Broadcasting your media now...")
                all_users_chat_ids = users_collection.distinct("_id")
                sent_count = 0
                failed_count = 0
                for user_chat_id_str in all_users_chat_ids:
                    try:
                        user_chat_id = int(user_chat_id_str)
                        if user_chat_id == ADMIN_ID:
                            continue
                        bot_obj.copy_message(user_chat_id, message.chat.id, message.message_id)
                        sent_count += 1
                        time.sleep(0.1)
                    except telebot.apihelper.ApiTelegramException as e:
                        logging.error(f"Failed to send broadcast to user {user_chat_id}: {e}")
                        failed_count += 1
                    except Exception as e:
                        logging.error(f"Unexpected error broadcasting to user {user_chat_id}: {e}")
                        failed_count += 1
                bot_obj.send_message(message.chat.id, f"Broadcast complete! Successfully sent to {sent_count} users. Failed for {failed_count} users.")
                bot_obj.send_message(
                    message.chat.id,
                    "What else, Admin?",
                    reply_markup=build_admin_keyboard()
                )
                return
            handle_media_common(message, bot_obj, bot_token)
        except Exception:
            logging.exception("Error in handle_media_types")

    @bot_obj.callback_query_handler(func=lambda c: c.data and c.data.startswith("admin_"))
    def admin_inline_callback(call):
        try:
            if call.from_user.id != ADMIN_ID:
                bot_obj.answer_callback_query(call.id, "Unauthorized", show_alert=True)
                return
            if call.data == "admin_total_users":
                total_users = users_collection.count_documents({})
                bot_obj.edit_message_text(f"Total users registered: {total_users}", chat_id=call.message.chat.id, message_id=call.message.message_id)
                bot_obj.send_message(
                    call.message.chat.id,
                    "What else, Admin?",
                    reply_markup=build_admin_keyboard()
                )
                bot_obj.answer_callback_query(call.id)
            elif call.data == "admin_send_broadcast":
                admin_broadcast_state[call.message.chat.id] = True
                bot_obj.send_message(call.message.chat.id, "Okay, Admin. Send me the message (text, photo, video, document, etc.) you want to broadcast to all users. To cancel, type /cancel_broadcast")
                bot_obj.answer_callback_query(call.id, "Send your broadcast message now")
            else:
                bot_obj.answer_callback_query(call.id)
        except Exception:
            logging.exception("Error in admin_inline_callback")

    @bot_obj.message_handler(content_types=['text'], func=lambda message: message.chat.id == ADMIN_ID and message.text == "Total Users")
    def handle_total_users(message):
        try:
            total_users = users_collection.count_documents({})
            bot_obj.send_message(message.chat.id, f"Total users registered: {total_users}")
            bot_obj.send_message(
                message.chat.id,
                "What else, Admin?",
                reply_markup=build_admin_keyboard()
            )
        except Exception:
            logging.exception("Error in handle_total_users")

    @bot_obj.message_handler(content_types=['text'], func=lambda message: message.chat.id == ADMIN_ID and message.text == "Send Broadcast")
    def handle_send_broadcast(message):
        try:
            admin_broadcast_state[message.chat.id] = True
            bot_obj.send_message(message.chat.id, "Okay, Admin. Send me the message (text, photo, video, document, etc.) you want to broadcast to all users. To cancel, type /cancel_broadcast")
        except Exception:
            logging.exception("Error in handle_send_broadcast")

    @bot_obj.message_handler(commands=['cancel_broadcast'], func=lambda message: message.chat.id == ADMIN_ID and admin_broadcast_state.get(message.chat.id, False))
    def cancel_broadcast(message):
        try:
            if message.chat.id in admin_broadcast_state:
                del admin_broadcast_state[message.chat.id]
            bot_obj.send_message(
                message.chat.id,
                "Broadcast cancelled. What else, Admin?",
                reply_markup=build_admin_keyboard()
            )
        except Exception:
            logging.exception("Error in cancel_broadcast")

    @bot_obj.message_handler(content_types=['text', 'photo', 'video', 'document', 'audio', 'voice'], func=lambda message: message.chat.id == ADMIN_ID and admin_broadcast_state.get(message.chat.id, False))
    def handle_broadcast_message(message):
        try:
            if message.chat.id in admin_broadcast_state:
                del admin_broadcast_state[message.chat.id]
            bot_obj.send_message(message.chat.id, "Broadcasting your message now...")
            all_users_chat_ids = users_collection.distinct("_id")
            sent_count = 0
            failed_count = 0
            for user_chat_id_str in all_users_chat_ids:
                try:
                    user_chat_id = int(user_chat_id_str)
                    if user_chat_id == ADMIN_ID:
                        continue
                    bot_obj.copy_message(user_chat_id, message.chat.id, message.message_id)
                    sent_count += 1
                    time.sleep(0.1)
                except telebot.apihelper.ApiTelegramException as e:
                    logging.error(f"Failed to send broadcast to user {user_chat_id}: {e}")
                    failed_count += 1
                except Exception as e:
                    logging.error(f"Unexpected error broadcasting to user {user_chat_id}: {e}")
                    failed_count += 1
            bot_obj.send_message(message.chat.id, f"Broadcast complete! Successfully sent to {sent_count} users. Failed for {failed_count} users.")
            bot_obj.send_message(
                message.chat.id,
                "What else, Admin?",
                reply_markup=build_admin_keyboard()
            )
        except Exception:
            logging.exception("Error in handle_broadcast_message")

    @bot_obj.message_handler(content_types=['text'])
    def handle_text_messages(message):
        try:
            if message.chat.id == ADMIN_ID and not admin_broadcast_state.get(message.chat.id, False):
                bot_obj.send_message(
                    message.chat.id,
                    "Admin, please use the admin options.",
                    reply_markup=build_admin_keyboard()
                )
                return
            bot_obj.send_message(message.chat.id, "I can only process voice, audio, video, files for transcription. Please send one of those, or use /lang to change your language settings.")
        except Exception:
            logging.exception("Error in handle_text_messages")

    @bot_obj.callback_query_handler(lambda c: c.data and c.data.startswith("get_key_points|"))
    def get_key_points_callback(call):
        try:
            parts = call.data.split("|")
            if len(parts) == 3:
                _, chat_id_part, msg_id_part = parts
            elif len(parts) == 2:
                _, msg_id_part = parts
                chat_id_part = str(call.message.chat.id)
            else:
                bot_obj.answer_callback_query(call.id, "Invalid request", show_alert=True)
                return
            try:
                chat_id_val = int(chat_id_part)
                msg_id = int(msg_id_part)
            except Exception:
                bot_obj.answer_callback_query(call.id, "Invalid message id", show_alert=True)
                return
            uid_key = str(chat_id_val)
            stored = user_transcriptions.get(uid_key, {}).get(msg_id)
            if not stored:
                bot_obj.answer_callback_query(call.id, " ⚠️ Get Summarize unavailable (maybe expired).", show_alert=True)
                return
            bot_obj.answer_callback_query(call.id, "Generating...")
            status_msg = bot_obj.send_message(call.message.chat.id, "🔄 Generating summary Text, please wait...", reply_to_message_id=call.message.message_id)
            prompt = f"Summarize this text iusing the same language it is written in without adding any introductions, notes, or extra phrases.\n\n{stored}"
            try:
                summary = ask_deepseek_r1(prompt)
            except Exception as e:
                logging.exception("Deepseek request failed")
                bot_obj.edit_message_text("😓 Failed to generating.", chat_id=call.message.chat.id, message_id=status_msg.message_id)
                return
            if not summary:
                bot_obj.edit_message_text("No Summary returned.", chat_id=call.message.chat.id, message_id=status_msg.message_id)
            else:
                bot_obj.edit_message_text(f"Summary Text hera💗:\n\n{summary}", chat_id=call.message.chat.id, message_id=status_msg.message_id)
        except Exception:
            logging.exception("Error in get_key_points_callback")

for idx, bot_obj in enumerate(bots):
    register_handlers(bot_obj, BOT_TOKENS[idx])

@app.route("/", methods=["GET", "POST", "HEAD"])
def webhook_root():
    if request.method in ("GET", "HEAD"):
        bot_index = request.args.get("bot_index")
        try:
            bot_index_val = int(bot_index) if bot_index is not None else 0
        except Exception:
            bot_index_val = 0
        now_iso = datetime.utcnow().isoformat() + "Z"
        return jsonify({"status": "ok", "time": now_iso, "bot_index": bot_index_val}), 200
    if request.method == "POST":
        content_type = request.headers.get("Content-Type", "")
        if content_type and content_type.startswith("application/json"):
            raw = request.get_data().decode("utf-8")
            try:
                payload = json.loads(raw)
            except Exception:
                payload = None
            bot_index = request.args.get("bot_index")
            if not bot_index and isinstance(payload, dict):
                bot_index = payload.get("bot_index")
            header_idx = request.headers.get("X-Bot-Index")
            if header_idx:
                bot_index = header_idx
            try:
                bot_index_val = int(bot_index) if bot_index is not None else 0
            except Exception:
                bot_index_val = 0
            if bot_index_val < 0 or bot_index_val >= len(bots):
                return abort(404)
            try:
                update = telebot.types.Update.de_json(raw)
                bots[bot_index_val].process_new_updates([update])
            except Exception:
                logging.exception("Error processing incoming webhook update")
            return "", 200
    return abort(403)

@app.route("/set_webhook", methods=["GET", "POST"])
def set_webhook_route():
    results = []
    for idx, bot_obj in enumerate(bots):
        try:
            url = WEBHOOK_BASE.rstrip("/") + f"/?bot_index={idx}"
            bot_obj.delete_webhook()
            time.sleep(0.2)
            bot_obj.set_webhook(url=url)
            results.append({"index": idx, "url": url, "status": "ok"})
        except Exception as e:
            logging.error(f"Failed to set webhook for bot {idx}: {e}")
            results.append({"index": idx, "error": str(e)})
    return jsonify({"results": results}), 200

@app.route("/delete_webhook", methods=["GET", "POST"])
def delete_webhook_route():
    results = []
    for idx, bot_obj in enumerate(bots):
        try:
            bot_obj.delete_webhook()
            results.append({"index": idx, "status": "deleted"})
        except Exception as e:
            logging.error(f"Failed to delete webhook for bot {idx}: {e}")
            results.append({"index": idx, "error": str(e)})
    return jsonify({"results": results}), 200

def set_webhook_on_startup():
    for idx, bot_obj in enumerate(bots):
        try:
            bot_obj.delete_webhook()
            time.sleep(0.2)
            url = WEBHOOK_BASE.rstrip("/") + f"/?bot_index={idx}"
            bot_obj.set_webhook(url=url)
            logging.info(f"Main bot webhook set successfully to {url}")
        except Exception as e:
            logging.error(f"Failed to set main bot webhook on startup: {e}")

def set_bot_info_and_startup():
    set_webhook_on_startup()

if __name__ == "__main__":
    try:
        set_bot_info_and_startup()
        try:
            client.admin.command('ping')
            logging.info("Successfully connected to MongoDB!")
        except Exception as e:
            logging.error("Could not connect to MongoDB: %s", e)
    except Exception:
        logging.exception("Failed during startup")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
