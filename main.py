import os
import subprocess
import logging
import time
import requests
import json
import tempfile
from moviepy.video.io.VideoFileClip import VideoFileClip
import edge_tts
import asyncio
import telebot
from telebot import types
from flask import Flask, request
from pydub import AudioSegment

# ====== CONFIG - read from env first (safer) ======
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN") or "8371007825:AAFpp_SVygKKTR6y0PlX9W4q9LBrgwLA6b8"
ASSEMBLYAI_KEY = os.environ.get("ASSEMBLYAI_KEY") or "a356bbda79da4fd8a77a12ad819c47e2"
GEMINI_KEY = os.environ.get("GEMINI_KEY") or "AIzaSyDLxRqMWmjpLW0IRh85JwLdLcYMEWY0_kk"
WEBHOOK_URL = os.environ.get("WEBHOOK_URL") or "https://download-bot-5sv5.onrender.com"

if any(k.startswith("AIza") or k.strip()=="" for k in [TELEGRAM_TOKEN, ASSEMBLYAI_KEY, GEMINI_KEY]):
    logging.warning("Make sure to rotate/regenerate exposed API keys and set them as environment variables (TELEGRAM_TOKEN, ASSEMBLYAI_KEY, GEMINI_KEY).")

# ===== ffmpeg detection =====
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

# ===== languages (same as yours) =====
SOURCE_LANGS = ['English', 'Arabic', 'Spanish', 'French', 'German', 'Italian', 'Portuguese', 'Russian', 'Chinese', 'Hindi']
DUB_LANGS = ["English","Arabic","Somali","Spanish","French","German","Italian","Portuguese","Russian","Chinese","Hindi","Urdu","Bengali","Punjabi",
"Indonesian","Malay","Turkish","Vietnamese","Thai","Japanese","Korean","Persian","Swahili","Amharic","Yoruba","Hausa","Igbo","Zulu","Xhosa",
"Afrikaans","Dutch","Polish","Czech","Slovak","Hungarian","Romanian","Bulgarian","Serbian","Croatian","Bosnian","Slovenian","Greek","Albanian",
"Macedonian","Lithuanian","Latvian","Estonian","Finnish","Swedish","Norwegian","Danish","Icelandic","Hebrew","Nepali","Sinhala","Khmer","Lao",
"Mongolian","Tibetan","Burmese","Filipino","Tagalog","Catalan","Basque","Galician","Ukrainian","Belarusian","Georgian","Armenian","Azerbaijani",
"Kazakh","Uzbek","Turkmen","Kyrgyz","Tajik","Malayalam","Kannada","Tamil","Telugu","Marathi","Gujarati","Odia","Assamese","Sindhi","Kurdish",
"Pashto","Kinyarwanda","Kirundi","Sesotho","Setswana","Lingala","Shona","Tigrinya","Fijian","Samoan","Tongan","Haitian Creole","Luxembourgish"]

LANG_CODE_ASR = {'English': 'en','Arabic': 'ar','Spanish': 'es','French': 'fr','German': 'de','Italian': 'it','Portuguese': 'pt','Russian': 'ru','Chinese': 'zh','Hindi': 'hi'}

TTS_VOICE_SINGLE = "en-US-PhoebeMultilingualNeural"
TTS_VOICES = {lang: TTS_VOICE_SINGLE for lang in SOURCE_LANGS}
TTS_VOICES["Somali"] = "so-SO-MuuseNeural"

user_data = {}
bot = telebot.TeleBot(TELEGRAM_TOKEN)
app = Flask(__name__)

# ===== Helper: extract audio (wav 16k mono) =====
def ensure_ffmpeg():
    global FFMPEG_BINARY
    if FFMPEG_BINARY:
        return FFMPEG_BINARY
    for p in POSSIBLE_FFMPEG_PATHS:
        if not p:
            continue
        try:
            subprocess.run([p, "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
            FFMPEG_BINARY = p
            return p
        except Exception:
            continue
    raise RuntimeError("ffmpeg not found")

def extract_audio_to_wav(video_path):
    ffmpeg = ensure_ffmpeg()
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp_name = tmp.name
    tmp.close()
    cmd = [ffmpeg, "-y", "-i", video_path, "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", tmp_name]
    subprocess.run(cmd, check=True)
    return tmp_name

# ===== AssemblyAI: upload audio and request transcription with timestamps =====
def assemblyai_upload(file_path):
    headers = {'authorization': ASSEMBLYAI_KEY}
    upload_url = 'https://api.assemblyai.com/v2/upload'
    with open(file_path, 'rb') as f:
        resp = requests.post(upload_url, headers=headers, data=f, timeout=180)
    resp.raise_for_status()
    return resp.json().get('upload_url')

def assemblyai_transcribe(upload_url, language_code='en'):
    url = 'https://api.assemblyai.com/v2/transcript'
    headers = {'authorization': ASSEMBLYAI_KEY, 'content-type': 'application/json'}
    body = {
        "audio_url": upload_url,
        "language_code": language_code,
        "punctuate": True,
        "format_text": True,
        "speaker_labels": True  # optional; remove if not needed
    }
    resp = requests.post(url, headers=headers, json=body, timeout=30)
    resp.raise_for_status()
    return resp.json()['id']

def poll_transcript(transcript_id, timeout=300):
    url = f'https://api.assemblyai.com/v2/transcript/{transcript_id}'
    headers = {'authorization': ASSEMBLYAI_KEY}
    start = time.time()
    while True:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        status = data.get('status')
        if status == 'completed':
            return data
        if status == 'failed':
            raise RuntimeError("Transcription failed: " + str(data.get('error')))
        if time.time() - start > timeout:
            raise TimeoutError("Transcription timed out")
        time.sleep(2)

# ===== Utilities: build segments from transcript =====
def build_segments_from_transcript(transcript_json):
    # Prefer utterances if available (AssemblyAI often returns 'utterances')
    segments = []
    if 'utterances' in transcript_json and transcript_json['utterances']:
        for u in transcript_json['utterances']:
            segments.append({
                'text': u.get('text', '').strip(),
                'start': u.get('start', 0),  # ms
                'end': u.get('end', 0)
            })
        return segments
    # fallback to words grouping: group by pause > 400ms
    words = transcript_json.get('words') or []
    if not words:
        # ultimate fallback: whole transcript as one segment
        return [{'text': transcript_json.get('text','').strip(), 'start': 0, 'end': int(transcript_json.get('audio_duration',0)*1000)}]
    group = []
    seg_start = None
    prev_end = None
    for w in words:
        w_start = w.get('start')
        w_end = w.get('end')
        if seg_start is None:
            seg_start = w_start
            group = [w.get('text', '')]
            prev_end = w_end
            continue
        gap = w_start - prev_end if w_start is not None and prev_end is not None else 0
        if gap > 400:  # treat as new segment
            segments.append({'text': ' '.join(group).strip(), 'start': seg_start, 'end': prev_end})
            seg_start = w_start
            group = [w.get('text','')]
        else:
            group.append(w.get('text',''))
        prev_end = w_end
    if group:
        segments.append({'text': ' '.join(group).strip(), 'start': seg_start, 'end': prev_end})
    return segments

# ===== Edge TTS wrapper (async) =====
async def generate_tts_edge(text, out_path, voice=TTS_VOICE_SINGLE):
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(out_path)

# ===== Build timeline audio (pydub overlay) =====
def build_timeline_audio(segments, total_duration_s, tts_files, output_wav_path, target_sample_rate=48000, target_channels=2):
    total_ms = int(total_duration_s * 1000)
    base = AudioSegment.silent(duration=total_ms, frame_rate=target_sample_rate)
    base = base.set_channels(target_channels)
    for i, seg in enumerate(segments):
        tts_path = tts_files[i]
        start_ms = int(seg['start'])  # AssemblyAI times are already in ms
        try:
            tts = AudioSegment.from_file(tts_path)
            tts = tts.set_frame_rate(target_sample_rate).set_channels(target_channels)
            base = base.overlay(tts, position=start_ms)
        except Exception as e:
            logging.exception("Failed overlaying tts file %s: %s", tts_path, e)
    # export
    base.export(output_wav_path, format="wav")
    return output_wav_path

# ===== Merge audio + video =====
def merge_audio_video(video_path, audio_path, output_path):
    ffmpeg_bin = FFMPEG_BINARY if FFMPEG_BINARY else "ffmpeg"
    cmd = [ffmpeg_bin, "-y", "-i", video_path, "-i", audio_path, "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", output_path]
    process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if process.returncode != 0:
        logging.error("FFmpeg error: %s", process.stderr.decode())
        return False
    return True

# ===== Gemini translation function (unchanged logic) =====
def send_gemini_translation(text, source_lang, target_lang):
    prompt_text = (
        f"Translate ONLY the following text from {source_lang} into {target_lang}.\n\n"
        "Important: Output ONLY the translated text. Do NOT include any explanatory notes, headings, "
        "labels, or delimiters. Do NOT add anything other than the translation.\n\n"
        f"Text:\n{text}"
    )
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
    headers = {"Content-Type": "application/json", "X-goog-api-key": GEMINI_KEY}
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

# ===== Bot handlers (most preserved) =====
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
        # safer tempfile
        tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        tmp.write(downloaded_file)
        tmp.flush()
        tmp.close()
        file_path = tmp.name
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
                try:
                    bot.answer_callback_query(call.id)
                except:
                    pass
                return
            user_data[user_id]['source_lang'] = lang
            markup = types.InlineKeyboardMarkup(row_width=2)
            buttons = [types.InlineKeyboardButton(text=lang2, callback_data=f"dub|{lang2}") for lang2 in DUB_LANGS]
            markup.add(*buttons)
            try:
                bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
            except Exception:
                pass
            try:
                bot.answer_callback_query(call.id)
            except:
                pass
        elif data.startswith("dub|"):
            lang = data.split("|", 1)[1]
            if user_id not in user_data or 'source_lang' not in user_data[user_id]:
                try:
                    bot.answer_callback_query(call.id)
                except:
                    pass
                return
            user_data[user_id]['dub_lang'] = lang
            try:
                bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, text="Processing...")
                status = {'chat_id': call.message.chat.id, 'message_id': call.message.message_id}
                user_data[user_id]['status_msg'] = status
            except Exception:
                pass
            try:
                bot.answer_callback_query(call.id)
            except:
                pass
            # NOTE: this will create and run a new event loop per job like your original code.
            asyncio.run(process_video(call.message.chat.id, user_data[user_id]))
    except Exception as e:
        logging.exception("Error in callback handler")
        try:
            bot.answer_callback_query(call.id)
        except:
            pass

# ===== check video size/duration function reused from your original code =====
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

# ===== The core processing pipeline (timestamps-based dubbing) =====
async def process_video(chat_id, data):
    video_path = data['video_path']
    source_lang = data['source_lang']
    dub_lang = data['dub_lang']
    status_info = data.get('status_msg')
    try:
        if status_info:
            try:
                bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text="Processing: extracting audio...")
            except Exception:
                pass

        # 1) Extract audio to WAV (16k mono)
        audio_wav = extract_audio_to_wav(video_path)

        if status_info:
            try:
                bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text="Processing: uploading audio for transcription...")
            except Exception:
                pass

        # 2) Upload audio to AssemblyAI
        audio_url = assemblyai_upload(audio_wav)

        if status_info:
            try:
                bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text="Processing: submitting transcription...")
            except Exception:
                pass

        # 3) Submit transcription (request timestamps)
        trans_id = assemblyai_transcribe(audio_url, language_code=LANG_CODE_ASR.get(source_lang, 'en'))

        # 4) Poll for completion
        transcript_json = poll_transcript(trans_id, timeout=300)

        # 5) Build segments from transcript (use utterances or words)
        segments = build_segments_from_transcript(transcript_json)

        if status_info:
            bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text=f"Processing: {len(segments)} segments found, translating...")

        # 6) For each segment: translate (Gemini) then TTS (edge-tts)
        tts_files = []
        for idx, seg in enumerate(segments):
            text_src = seg['text']
            if not text_src.strip():
                # skip empty
                tts_files.append(None)
                continue
            translated = send_gemini_translation(text_src, source_lang, dub_lang)
            if not translated:
                translated = text_src  # fallback: use source text if translation fails
            # optional: you can clean punctuation here
            tts_tmp = tempfile.NamedTemporaryFile(suffix=f"_seg{idx}.mp3", delete=False)
            tts_tmp.close()
            # generate TTS (async)
            voice = TTS_VOICES.get(dub_lang, TTS_VOICE_SINGLE)
            await generate_tts_edge(translated, tts_tmp.name, voice=voice)
            tts_files.append(tts_tmp.name)
            if status_info:
                try:
                    bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text=f"Processing: generated TTS for segment {idx+1}/{len(segments)}")
                except Exception:
                    pass

        # 7) Build final timeline audio
        clip = VideoFileClip(video_path)
        total_dur = clip.duration
        clip.close()
        final_audio_wav = tempfile.NamedTemporaryFile(suffix="_final.wav", delete=False).name
        build_timeline_audio(segments, total_dur, tts_files, final_audio_wav)

        if status_info:
            bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text="Processing: merging audio and video...")

        # 8) Merge and send
        output_path = f'dubbed_{chat_id}.mp4'
        success = merge_audio_video(video_path, final_audio_wav, output_path)
        if not success:
            if status_info:
                bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text="Processing: Merge failed.")
            else:
                bot.send_message(chat_id, "Failed to merge audio and video.")
            return

        if status_info:
            bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text="Processing: Done. Sending video...")
        else:
            bot.send_message(chat_id, "Your dubbed video is ready. Sending now...")

        try:
            with open(output_path, 'rb') as vf:
                bot.send_video(chat_id, vf, supports_streaming=True)
        except Exception:
            bot.send_message(chat_id, "Failed to send video. Please try again.")

    except Exception as e:
        logging.exception("Error processing video")
        try:
            if status_info:
                bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text="Processing: An error occurred.")
            else:
                bot.send_message(chat_id, "An error occurred while processing your video. Please try again.")
        except Exception:
            pass
    finally:
        # cleanup
        try:
            if os.path.exists(video_path):
                os.remove(video_path)
        except Exception:
            pass
        try:
            if 'audio_wav' in locals() and os.path.exists(audio_wav):
                os.remove(audio_wav)
        except Exception:
            pass
        try:
            if 'final_audio_wav' in locals() and os.path.exists(final_audio_wav):
                os.remove(final_audio_wav)
        except Exception:
            pass
        try:
            for f in locals().get('tts_files', []) or []:
                if f and os.path.exists(f):
                    os.remove(f)
        except Exception:
            pass
        try:
            if os.path.exists(f'dubbed_{chat_id}.mp4'):
                os.remove(f'dubbed_{chat_id}.mp4')
        except Exception:
            pass
        try:
            if isinstance(chat_id, int) and chat_id in user_data:
                del user_data[chat_id]
        except Exception:
            pass

# ===== webhook handler and setup_webhook (unchanged) =====
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
