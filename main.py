


import os
import subprocess
import logging
import time
import requests
import json
import tempfile
import asyncio
import shutil

from moviepy.video.io.VideoFileClip import VideoFileClip
import edge_tts
import telebot
from telebot import types
from flask import Flask, request
from pydub import AudioSegment

# ----------------- Configuration (read from environment) -----------------
TELEGRAM_TOKEN = os.environ.get("8371007825:AAFpp_SVygKKTR6y0PlX9W4q9LBrgwLA6b8")
ASSEMBLYAI_KEY = os.environ.get("a356bbda79da4fd8a77a12ad819c47e2")
GEMINI_KEY = os.environ.get("AIzaSyDLxRqMWmjpLW0IRh85JwLdLcYMEWY0_kk")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://download-bot-5sv5.onrender.com")
FFMPEG_BINARY = os.environ.get("FFMPEG_BINARY", "")
GENTLE_URL = os.environ.get("GENTLE_URL", "http://127.0.0.1:8765")

# Basic checks
if not TELEGRAM_TOKEN or not ASSEMBLYAI_KEY or not GEMINI_KEY:
    logging.warning("One or more API keys are missing. Set TELEGRAM_TOKEN, ASSEMBLYAI_KEY, GEMINI_KEY as environment variables.")

# Detect ffmpeg if not set
if not FFMPEG_BINARY:
    POSSIBLE_FFMPEG_PATHS = ["./ffmpeg", "/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg", "ffmpeg"]
    for p in POSSIBLE_FFMPEG_PATHS:
        try:
            subprocess.run([p, "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
            FFMPEG_BINARY = p
            break
        except Exception:
            continue
if not FFMPEG_BINARY:
    logging.warning("ffmpeg not found. Please install ffmpeg or set FFMPEG_BINARY env var.")

# ----------------- Languages and voices -----------------
SOURCE_LANGS = ['English', 'Arabic', 'Spanish', 'French', 'German', 'Italian', 'Portuguese', 'Russian', 'Chinese', 'Hindi']
DUB_LANGS = ["English","Arabic","Somali","Spanish","French","German","Italian","Portuguese","Russian","Chinese","Hindi","Urdu","Bengali","Punjabi",
"Indonesian","Malay","Turkish","Vietnamese","Thai","Japanese","Korean","Persian","Swahili","Amharic","Yoruba","Hausa","Igbo","Zulu","Xhosa",
"Afrikaans","Dutch","Polish","Czech","Slovak","Hungarian","Romanian","Bulgarian","Serbian","Croatian","Bosnian","Slovenian","Greek","Albanian",
"Macedonian","Lithuanian","Latvian","Estonian","Finnish","Swedish","Norwegian","Danish","Icelandic","Hebrew","Nepali","Sinhala","Khmer","Lao",
"Mongolian","Tibetan","Burmese","Filipino","Tagalog","Catalan","Basque","Galician","Ukrainian","Belarusian","Georgian","Armenian","Azerbaijani",
"Kazakh","Uzbek","Turkmen","Kyrgyz","Tajik","Malayalam","Kannada","Tamil","Telugu","Marathi","Gujarati","Odia","Assamese","Sindhi","Kurdish",
"Pashto","Kinyarwanda","Kirundi","Sesotho","Setswana","Lingala","Shona","Tigrinya","Fijian","Samoan","Tongan","Haitian Creole","Luxembourgish"]

LANG_CODE_ASR = {
    'English': 'en', 'Arabic': 'ar', 'Spanish': 'es', 'French': 'fr',
    'German': 'de', 'Italian': 'it', 'Portuguese': 'pt', 'Russian': 'ru',
    'Chinese': 'zh', 'Hindi': 'hi'
}

TTS_VOICE_SINGLE = "en-US-PhoebeMultilingualNeural"
TTS_VOICES = {lang: TTS_VOICE_SINGLE for lang in SOURCE_LANGS}
TTS_VOICES["Somali"] = "so-SO-MuuseNeural"

# ----------------- Globals -----------------
user_data = {}
bot = telebot.TeleBot(TELEGRAM_TOKEN) if TELEGRAM_TOKEN else telebot.TeleBot("TEST_TOKEN")
app = Flask(__name__)

# ----------------- Utility functions -----------------
def ensure_ffmpeg():
    if FFMPEG_BINARY:
        return FFMPEG_BINARY
    raise RuntimeError("ffmpeg not found. Install or set FFMPEG_BINARY.")

def extract_audio_to_wav(video_path, out_rate=16000):
    ffmpeg = ensure_ffmpeg()
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp_name = tmp.name
    tmp.close()
    cmd = [ffmpeg, "-y", "-i", video_path, "-vn", "-acodec", "pcm_s16le", "-ar", str(out_rate), "-ac", "1", tmp_name]
    subprocess.run(cmd, check=True)
    return tmp_name

def assemblyai_upload(file_path):
    url = 'https://api.assemblyai.com/v2/upload'
    headers = {'authorization': ASSEMBLYAI_KEY}
    with open(file_path, 'rb') as f:
        resp = requests.post(url, headers=headers, data=f, timeout=180)
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
        "speaker_labels": False
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

# ----------------- Gentle forced-alignment helpers -----------------
def gentle_align(audio_path, transcript_text, gentle_url=GENTLE_URL, timeout=120):
    """
    Send audio + transcript to Gentle and return JSON alignment.
    Gentle endpoint: POST {gentle_url}/transcriptions?async=false
    """
    endpoint = f"{gentle_url}/transcriptions?async=false"
    with open(audio_path, 'rb') as f_audio:
        files = {
            'audio': ('audio.wav', f_audio, 'audio/wav'),
            'transcript': (None, transcript_text)
        }
        try:
            resp = requests.post(endpoint, files=files, timeout=timeout)
        except Exception:
            logging.exception("Failed to contact Gentle")
            return None
    if resp.status_code != 200:
        logging.error("Gentle returned %s: %s", resp.status_code, resp.text[:500])
        return None
    try:
        return resp.json()
    except Exception:
        logging.exception("Failed to parse Gentle JSON")
        return None

def build_segments_from_gentle(alignment_json, gap_ms=300, min_segment_words=1):
    """
    Build phrase segments from Gentle alignment words.
    Returns segments list with 'text', 'start' (ms), 'end' (ms).
    """
    words = alignment_json.get('words', [])
    segments = []
    current_words = []
    seg_start = None
    prev_end_ms = None

    for w in words:
        word_text = w.get('word', '').strip()
        # Some words may be unaligned (no start)
        if 'start' not in w or w['start'] is None:
            # attach to current if exists
            if prev_end_ms is None:
                continue
            current_words.append(word_text)
            continue

        w_start_ms = int(float(w['start']) * 1000)
        w_end_ms = int(float(w['end']) * 1000)

        if seg_start is None:
            seg_start = w_start_ms
            current_words = [word_text]
            prev_end_ms = w_end_ms
            continue

        gap = w_start_ms - prev_end_ms if prev_end_ms is not None else 0
        if gap > gap_ms:
            seg_text = ' '.join(current_words).strip()
            if len(current_words) >= min_segment_words and seg_text:
                segments.append({'text': seg_text, 'start': seg_start, 'end': prev_end_ms})
            seg_start = w_start_ms
            current_words = [word_text]
            prev_end_ms = w_end_ms
        else:
            current_words.append(word_text)
            prev_end_ms = w_end_ms

    if current_words and seg_start is not None:
        seg_text = ' '.join(current_words).strip()
        segments.append({'text': seg_text, 'start': seg_start, 'end': prev_end_ms})

    # fallback
    if not segments:
        full_text = ' '.join([w.get('word','') for w in words]).strip()
        if full_text:
            first = next((w for w in words if 'start' in w and w['start'] is not None), None)
            last = next((w for w in reversed(words) if 'end' in w and w['end'] is not None), None)
            start_ms = int(first['start']*1000) if first else 0
            end_ms = int(last['end']*1000) if last else start_ms + 1000
            segments = [{'text': full_text, 'start': start_ms, 'end': end_ms}]
    return segments

# ----------------- TTS and audio timeline -----------------
async def generate_tts_edge(text, out_path, voice=TTS_VOICE_SINGLE):
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(out_path)

def build_timeline_audio(segments, total_duration_s, tts_files, output_wav_path, target_sample_rate=48000, target_channels=2):
    total_ms = int(total_duration_s * 1000)
    base = AudioSegment.silent(duration=total_ms, frame_rate=target_sample_rate)
    base = base.set_channels(target_channels)
    for i, seg in enumerate(segments):
        if i >= len(tts_files) or not tts_files[i]:
            continue
        tts_path = tts_files[i]
        start_ms = int(seg['start'])
        try:
            tts = AudioSegment.from_file(tts_path)
            tts = tts.set_frame_rate(target_sample_rate).set_channels(target_channels)
            base = base.overlay(tts, position=start_ms)
        except Exception:
            logging.exception("Overlay failed for %s", tts_path)
    base.export(output_wav_path, format="wav")
    return output_wav_path

def merge_audio_video(video_path, audio_path, output_path):
    ffmpeg_bin = FFMPEG_BINARY or "ffmpeg"
    cmd = [ffmpeg_bin, "-y", "-i", video_path, "-i", audio_path,
           "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
           "-shortest", "-movflags", "+faststart", output_path]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        logging.error("FFmpeg error: %s", proc.stderr.decode(errors='ignore'))
        return False
    return True

# ----------------- Gemini translation (unchanged logic) -----------------
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

# ----------------- Bot handlers -----------------
@bot.message_handler(commands=['start'])
def start_cmd(message):
    bot.send_message(message.chat.id, "Welcome! Send your video and I will translate and dub it for you. Please send a video file.")

@bot.message_handler(content_types=['video'])
def handle_video(message):
    try:
        file_info = bot.get_file(message.video.file_id)
        if not file_info or not file_info.file_path:
            bot.send_message(message.chat.id, "Couldn't get file info. Try again.")
            return
        downloaded = bot.download_file(file_info.file_path)
        tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        tmp.write(downloaded)
        tmp.flush()
        tmp.close()
        video_path = tmp.name
        valid, msg = check_video_size_duration(video_path)
        if not valid:
            bot.send_message(message.chat.id, f"Warning: {msg}")
            os.remove(video_path)
            return
        user_id = message.from_user.id
        user_data[user_id] = {'video_path': video_path}
        markup = types.InlineKeyboardMarkup(row_width=2)
        buttons = [types.InlineKeyboardButton(text=lang, callback_data=f"src|{lang}") for lang in SOURCE_LANGS]
        markup.add(*buttons)
        bot.send_message(message.chat.id, "Select the original language spoken in the video:", reply_markup=markup)
    except Exception:
        logging.exception("handle_video failed")
        bot.send_message(message.chat.id, "An error occurred while processing your video. Please try again.")

@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    try:
        user_id = call.from_user.id
        data = call.data or ""
        if data.startswith("src|"):
            lang = data.split("|",1)[1]
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
            lang = data.split("|",1)[1]
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
            # note: this uses asyncio.run for the pipeline (one-off). For production, prefer background worker.
            asyncio.run(process_video(call.message.chat.id, user_data[user_id]))

# ----------------- Reused helper from earlier -----------------
def check_video_size_duration(file_path):
    max_size = 20 * 1024 * 1024  # 20 MB
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

# ----------------- Core processing pipeline (Gentle-integrated) -----------------
async def process_video(chat_id, data):
    video_path = data['video_path']
    source_lang = data['source_lang']
    dub_lang = data['dub_lang']
    status_info = data.get('status_msg')
    audio_wav = None
    final_audio_wav = None
    tts_files = []
    output_path = f'dubbed_{chat_id}.mp4'
    try:
        if status_info:
            try:
                bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text="Processing: extracting audio...")
            except:
                pass

        audio_wav = extract_audio_to_wav(video_path)

        if status_info:
            try:
                bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text="Processing: uploading audio for transcription...")
            except:
                pass

        upload_url = assemblyai_upload(audio_wav)

        if status_info:
            try:
                bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text="Processing: submitting transcription...")
            except:
                pass

        trans_id = assemblyai_transcribe(upload_url, language_code=LANG_CODE_ASR.get(source_lang, 'en'))

        transcript_json = poll_transcript(trans_id, timeout=300)
        transcript_text = transcript_json.get('text','').strip()

        if status_info:
            try:
                bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text="Processing: running forced-alignment (Gentle)...")
            except:
                pass

        align_json = gentle_align(audio_wav, transcript_text)
        if not align_json:
            # fallback to simple assemblyai segments if Gentle failed
            if status_info:
                try:
                    bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text="Gentle failed; falling back to ASR segmentation...")
                except:
                    pass
            # fallback building: use AssemblyAI utterances or words grouping
            segments = build_segments_from_asr(transcript_json)
        else:
            segments = build_segments_from_gentle(align_json, gap_ms=250)

        if status_info:
            try:
                bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text=f"Processing: {len(segments)} segments - translating and generating TTS...")
            except:
                pass

        # per-segment translate + TTS
        for idx, seg in enumerate(segments):
            txt = seg.get('text','').strip()
            if not txt:
                tts_files.append(None)
                continue
            translated = send_gemini_translation(txt, source_lang, dub_lang)
            if not translated:
                translated = txt  # fallback
            tts_tmp = tempfile.NamedTemporaryFile(suffix=f"_seg{idx}.mp3", delete=False)
            tts_tmp.close()
            voice = TTS_VOICES.get(dub_lang, TTS_VOICE_SINGLE)
            await generate_tts_edge(translated, tts_tmp.name, voice=voice)
            tts_files.append(tts_tmp.name)
            if status_info:
                try:
                    bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text=f"Processing: TTS {idx+1}/{len(segments)}")
                except:
                    pass

        # build timeline audio with overlays
        clip = VideoFileClip(video_path)
        total_dur = clip.duration
        clip.close()

        final_audio_wav = tempfile.NamedTemporaryFile(suffix="_final.wav", delete=False).name
        build_timeline_audio(segments, total_dur, tts_files, final_audio_wav)

        if status_info:
            try:
                bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text="Processing: merging audio and video...")
            except:
                pass

        ok = merge_audio_video(video_path, final_audio_wav, output_path)
        if not ok:
            if status_info:
                bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text="Processing: merge failed.")
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

    except Exception:
        logging.exception("process_video failed")
        try:
            if status_info:
                bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text="Processing: An error occurred.")
            else:
                bot.send_message(chat_id, "An internal error occurred while processing your video.")
        except:
            pass
    finally:
        # cleanup files
        for p in [audio_wav, final_audio_wav]:
            try:
                if p and os.path.exists(p):
                    os.remove(p)
            except:
                pass
        try:
            for f in tts_files:
                if f and os.path.exists(f):
                    os.remove(f)
        except:
            pass
        try:
            if os.path.exists(output_path):
                os.remove(output_path)
        except:
            pass
        try:
            if isinstance(chat_id, int) and chat_id in user_data:
                del user_data[chat_id]
        except:
            pass

# ----------------- Fallback ASR segments builder (simple) -----------------
def build_segments_from_asr(transcript_json, gap_ms=400):
    # Use 'utterances' if available, otherwise group words by gaps
    segments = []
    if 'utterances' in transcript_json and transcript_json['utterances']:
        for u in transcript_json['utterances']:
            start = u.get('start', 0)
            end = u.get('end', 0)
            segments.append({'text': u.get('text','').strip(), 'start': int(start), 'end': int(end)})
        return segments
    words = transcript_json.get('words') or []
    if not words:
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
        gap = (w_start - prev_end) if (w_start is not None and prev_end is not None) else 0
        if gap > gap_ms:
            segments.append({'text': ' '.join(group).strip(), 'start': int(seg_start), 'end': int(prev_end)})
            seg_start = w_start
            group = [w.get('text','')]
        else:
            group.append(w.get('text',''))
        prev_end = w_end
    if group:
        segments.append({'text': ' '.join(group).strip(), 'start': int(seg_start), 'end': int(prev_end)})
    return segments

# ----------------- Webhook handler and setup -----------------
@app.route('/', methods=['POST'])
def webhook_handler():
    try:
        json_str = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_str)
        bot.process_new_updates([update])
    except Exception:
        logging.exception("Failed to process incoming webhook")
    return '', 200

def setup_webhook():
    if not WEBHOOK_URL:
        logging.info("WEBHOOK_URL not set; running without webhook.")
        return
    try:
        bot.remove_webhook()
        success = bot.set_webhook(WEBHOOK_URL)
        if not success:
            logging.error("Failed to set webhook to %s", WEBHOOK_URL)
        else:
            logging.info("Webhook set to %s", WEBHOOK_URL)
    except Exception:
        logging.exception("Error setting webhook")

# ----------------- Main -----------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Starting Telegram dubbing bot...")
    setup_webhook()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
