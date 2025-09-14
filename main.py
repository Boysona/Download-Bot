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

TELEGRAM_TOKEN = "8371007825:AAFpp_SVygKKTR6y0PlX9W4q9LBrgwLA6b8"
ASSEMBLYAI_KEY = "a356bbda79da4fd8a77a12ad819c47e2"
GEMINI_KEY = "AIzaSyDLxRqMWmjpLW0IRh85JwLdLcYMEWY0_kk"

WEBHOOK_URL = "https://download-bot-5sv5.onrender.com"

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
TTS_VOICES = {lang: TTS_VOICE_SINGLE for lang in SOURCE_LANGS}

user_data = {}

bot = telebot.TeleBot(TELEGRAM_TOKEN)

app = Flask(__name__)

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
    except Exception as e:
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
    except Exception as e:
        logging.exception("Error parsing Gemini response")
        return None

def check_video_size_duration(file_path):
    max_size = 20 * 1024 * 1024
    max_duration = 120
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
    cmd = f'{ffmpeg_bin} -y -i "{video_path}" -i "{audio_path}" -map 0:v:0 -map 1:a:0 -c:v copy -c:a aac -shortest -movflags +faststart "{output_path}"'
    process = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if process.returncode != 0:
        logging.error("FFmpeg error: %s", process.stderr.decode())
    return process.returncode == 0

def transcode_video(input_path, output_path, target_label):
    ffmpeg_bin = FFMPEG_BINARY if FFMPEG_BINARY else "ffmpeg"
    scale = None
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
    cmd = f'{ffmpeg_bin} -y -i "{input_path}" -vf "scale={scale}" -c:v libx264 -crf 23 -preset veryfast -c:a aac -movflags +faststart "{output_path}"'
    process = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if process.returncode != 0:
        logging.error("FFmpeg transcode error: %s", process.stderr.decode())
    return process.returncode == 0

@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(message.chat.id, "Welcome! Send your video and I will translate and dub it for you. Please start by sending a video file.")

@bot.message_handler(content_types=['video'])
def handle_video(message):
    try:
        file_info = bot.get_file(message.video.file_id)
        if file_info.file_path is None:
            bot.send_message(message.chat.id, "Sorry, I couldn't process that video. Please try another file.")
            return
        downloaded_file = bot.download_file(file_info.file_path)
        file_path = f'temp_{message.from_user.id}.mp4'
        with open(file_path, 'wb') as f:
            f.write(downloaded_file)
        valid, msg = check_video_size_duration(file_path)
        if not valid:
            bot.send_message(message.chat.id, f"Warning: {msg}")
            os.remove(file_path)
            return
        user_id = message.from_user.id
        user_data[user_id] = {'video_path': file_path}
        markup = types.InlineKeyboardMarkup(row_width=2)
        buttons = [types.InlineKeyboardButton(text=lang, callback_data=f"src|{lang}") for lang in SOURCE_LANGS]
        markup.add(*buttons)
        bot.send_message(message.chat.id, "Select the original language spoken in the video:", reply_markup=markup)
    except Exception as e:
        logging.exception("Error handling video")
        bot.send_message(message.chat.id, "An error occurred while processing your video. Please try again.")

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
            bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, text=f"Source language set to: {lang}\nNow choose the dubbing language.")
            markup = types.InlineKeyboardMarkup(row_width=2)
            buttons = [types.InlineKeyboardButton(text=lang2, callback_data=f"dub|{lang2}") for lang2 in DUB_LANGS]
            markup.add(*buttons)
            bot.send_message(call.message.chat.id, "Choose the dubbing language:", reply_markup=markup)
            bot.answer_callback_query(call.id, f"Source language saved: {lang}")
        elif data.startswith("dub|"):
            lang = data.split("|", 1)[1]
            if user_id not in user_data or 'source_lang' not in user_data[user_id]:
                bot.answer_callback_query(call.id, "Session expired or source language missing. Please send video again.")
                return
            user_data[user_id]['dub_lang'] = lang
            bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, text=f"Dubbing language set to: {lang}\nPreparing to process your video...")
            status = bot.send_message(call.message.chat.id, "Status: Queued. Preparing to process your video...")
            user_data[user_id]['status_msg'] = {'chat_id': status.chat.id, 'message_id': status.message_id}
            bot.answer_callback_query(call.id, f"Dubbing language saved: {lang}")
            asyncio.run(process_video(call.message.chat.id, user_data[user_id]))
        elif data.startswith("quality|"):
            parts = data.split("|", 1)
            if len(parts) < 2:
                bot.answer_callback_query(call.id, "Invalid selection.")
                return
            quality = parts[1]
            if user_id not in user_data or 'final_output' not in user_data[user_id]:
                bot.answer_callback_query(call.id, "Session expired or output missing. Please process a new video.")
                return
            bot.answer_callback_query(call.id, f"Preparing your video in {quality}.")
            status_info = user_data[user_id].get('status_msg')
            if status_info:
                try:
                    bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text=f"Status: Transcoding to {quality}...")
                except Exception:
                    pass
            input_path = user_data[user_id]['final_output']
            out_path = f'final_{user_id}_{quality}.mp4'
            success = transcode_video(input_path, out_path, quality)
            if not success:
                bot.send_message(call.message.chat.id, "Failed to transcode video. Sending original quality instead.")
                out_path = input_path
            try:
                with open(out_path, 'rb') as vf:
                    bot.send_video(call.message.chat.id, vf, supports_streaming=True)
            except Exception:
                bot.send_message(call.message.chat.id, "Failed to send video. Please try again.")
            try:
                if os.path.exists(out_path) and out_path != input_path:
                    os.remove(out_path)
            except Exception:
                pass
            if 'final_output' in user_data[user_id]:
                try:
                    os.remove(user_data[user_id]['final_output'])
                except Exception:
                    pass
            if 'video_path' in user_data[user_id]:
                try:
                    os.remove(user_data[user_id]['video_path'])
                except Exception:
                    pass
            if 'tts_path' in user_data[user_id]:
                try:
                    os.remove(user_data[user_id]['tts_path'])
                except Exception:
                    pass
            if user_id in user_data:
                del user_data[user_id]
    except Exception as e:
        logging.exception("Error in callback handler")
        try:
            bot.answer_callback_query(call.id, "An unexpected error occurred.")
        except:
            pass

async def process_video(chat_id, data):
    video_path = data['video_path']
    source_lang = data['source_lang']
    dub_lang = data['dub_lang']
    status_info = data.get('status_msg')
    try:
        if status_info:
            try:
                bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text="Status: Uploading video for transcription...")
            except Exception:
                pass
        headers = {'authorization': ASSEMBLYAI_KEY, 'content-type': 'application/octet-stream'}
        with open(video_path, 'rb') as f:
            response = requests.post('https://api.assemblyai.com/v2/upload', headers=headers, data=f, timeout=180)
        if response.status_code not in (200, 201):
            if status_info:
                bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text="Status: Failed to upload video for transcription. Please try again.")
            else:
                bot.send_message(chat_id, "Failed to upload video for transcription. Please try again.")
            return
        audio_url = response.json()['upload_url']
        if status_info:
            try:
                bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text="Status: Submitted for transcription...")
            except Exception:
                pass
        trans_resp = requests.post(
            'https://api.assemblyai.com/v2/transcript',
            headers={'authorization': ASSEMBLYAI_KEY},
            json={'audio_url': audio_url, 'language_code': LANG_CODE_ASR.get(source_lang, 'en')}
        )
        if trans_resp.status_code not in (200, 201):
            if status_info:
                bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text="Status: Failed to start transcription. Please try again.")
            else:
                bot.send_message(chat_id, "Failed to start transcription. Please try again.")
            return
        trans_id = trans_resp.json()['id']
        max_retries = 60
        retry_count = 0
        text = ""
        while retry_count < max_retries:
            time.sleep(3)
            status_resp = requests.get(f'https://api.assemblyai.com/v2/transcript/{trans_id}',
                                     headers={'authorization': ASSEMBLYAI_KEY}, timeout=30)
            if status_resp.status_code != 200:
                if status_info:
                    bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text="Status: Error checking transcription status. Please try again.")
                else:
                    bot.send_message(chat_id, "Error checking transcription status. Please try again.")
                return
            status = status_resp.json()
            if status.get('status') == 'completed':
                text = status.get('text', '')
                break
            elif status.get('status') == 'failed':
                if status_info:
                    bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text="Status: Transcription failed. Please try again.")
                else:
                    bot.send_message(chat_id, "ASR failed. Please try again.")
                return
            retry_count += 1
        if retry_count >= max_retries:
            if status_info:
                bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text="Status: Transcription timed out. Try a shorter video.")
            else:
                bot.send_message(chat_id, "Transcription timed out. Please try again with a shorter video.")
            return
        if not text:
            if status_info:
                bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text="Status: No transcript returned.")
            else:
                bot.send_message(chat_id, "No transcript text returned.")
            return
        if status_info:
            bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text="Status: Translating text...")
        translated_text = send_gemini_translation(text, source_lang, dub_lang)
        if not translated_text:
            if status_info:
                bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text="Status: Translation failed. Please try again.")
            else:
                bot.send_message(chat_id, "Translation failed. Please try again.")
            return
        translated_text = translated_text.replace(".", ",")
        if status_info:
            bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text="Status: Generating speech audio...")
        tts_path = f'tts_{chat_id}.mp3'
        voice = TTS_VOICE_SINGLE
        await generate_tts(translated_text, tts_path, voice)
        if status_info:
            bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text="Status: Merging new audio with video...")
        output_path = f'dubbed_{chat_id}.mp4'
        success = merge_audio_video(video_path, tts_path, output_path)
        if not success:
            if status_info:
                bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text="Status: Failed to merge audio and video.")
            else:
                bot.send_message(chat_id, "Failed to merge audio and video.")
            return
        user_data[int(chat_id)]['final_output'] = output_path
        user_data[int(chat_id)]['tts_path'] = tts_path
        if status_info:
            bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text="Status: Done. Choose the video quality you prefer (smaller sizes use less data).")
        markup = types.InlineKeyboardMarkup(row_width=2)
        qbuttons = [
            types.InlineKeyboardButton(text="1080p", callback_data="quality|1080p"),
            types.InlineKeyboardButton(text="720p", callback_data="quality|720p"),
            types.InlineKeyboardButton(text="640p", callback_data="quality|640p"),
            types.InlineKeyboardButton(text="256p", callback_data="quality|256p")
        ]
        markup.add(*qbuttons)
        bot.send_message(chat_id, "Select the desired output quality:", reply_markup=markup)
    except Exception as e:
        logging.exception("Error processing video")
        try:
            if status_info:
                bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text="Status: An error occurred during processing. Please try again.")
            else:
                bot.send_message(chat_id, "An error occurred while processing your video. Please try again.")
        except Exception:
            pass
    finally:
        pass

@app.route('/', methods=['POST'])
def webhook_handler():
    try:
        json_str = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_str)
        bot.process_new_updates([update])
    except Exception as e:
        logging.exception("Failed to process incoming webhook")
    return '', 200

def setup_webhook():
    try:
        bot.remove_webhook()
        success = bot.set_webhook(WEBHOOK_URL)
        if not success:
            logging.error("Failed to set webhook to %s", WEBHOOK_URL)
        else:
            logging.info("Webhook set to %s", WEBHOOK_URL)
    except Exception:
        logging.exception("Error setting webhook")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Starting Telegram webhook bot...")
    setup_webhook()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
