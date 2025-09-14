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
# It's generally better to use secrets management or more secure methods for tokens
# than directly embedding them or relying solely on environment variables for sensitive keys.
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN") # Changed to TELEGRAM_TOKEN for clarity
ASSEMBLYAI_KEY = os.environ.get("ASSEMBLYAI_KEY")
GEMINI_KEY = os.environ.get("GEMINI_KEY")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://download-bot-5sv5.onrender.com")
FFMPEG_BINARY = os.environ.get("FFMPEG_BINARY", "")
GENTLE_URL = os.environ.get("GENTLE_URL", "http://127.0.0.1:8765")

# Basic checks
if not TELEGRAM_TOKEN or not ASSEMBLYAI_KEY or not GEMINI_KEY:
    logging.warning("One or more API keys are missing. Please ensure TELEGRAM_TOKEN, ASSEMBLYAI_KEY, and GEMINI_KEY are set as environment variables.")

# Detect ffmpeg if not set
if not FFMPEG_BINARY:
    POSSIBLE_FFMPEG_PATHS = ["./ffmpeg", "/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg", "ffmpeg"]
    for p in POSSIBLE_FFMPEG_PATHS:
        try:
            subprocess.run([p, "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
            FFMPEG_BINARY = p
            logging.info(f"ffmpeg found at: {FFMPEG_BINARY}")
            break
        except Exception:
            continue
if not FFMPEG_BINARY:
    logging.warning("ffmpeg not found. Please install ffmpeg or set the FFMPEG_BINARY environment variable.")

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

# Using a more generic voice for broader compatibility, but you can specify more
# specific ones if you know the exact Edge TTS voice names for each language.
# The provided 'en-US-PhoebeMultilingualNeural' might not work for all non-English languages.
# It's safer to use a general one or map them explicitly.
TTS_VOICE_DEFAULT = "en-US-JennyMultilingualNeural" # A good multilingual option
TTS_VOICES = {lang: TTS_VOICE_DEFAULT for lang in SOURCE_LANGS}
TTS_VOICES["Somali"] = "so-SO-MuuseNeural" # Specific voice for Somali

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
    """Extracts audio from a video file and saves it as a WAV file."""
    ffmpeg = ensure_ffmpeg()
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp_name = tmp.name
    tmp.close()
    logging.info(f"Extracting audio from {video_path} to {tmp_name}")
    cmd = [ffmpeg, "-y", "-i", video_path, "-vn", "-acodec", "pcm_s16le", "-ar", str(out_rate), "-ac", "1", tmp_name]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        logging.info(f"Audio extracted successfully to {tmp_name}")
        return tmp_name
    except subprocess.CalledProcessError as e:
        logging.error(f"ffmpeg extraction failed: {e.stderr.decode(errors='ignore')}")
        raise

def assemblyai_upload(file_path):
    """Uploads a file to AssemblyAI for transcription."""
    url = 'https://api.assemblyai.com/v2/upload'
    headers = {'authorization': ASSEMBLYAI_KEY}
    logging.info(f"Uploading audio file {file_path} to AssemblyAI...")
    try:
        with open(file_path, 'rb') as f:
            resp = requests.post(url, headers=headers, data=f, timeout=180)
        resp.raise_for_status()
        upload_url = resp.json().get('upload_url')
        logging.info(f"Audio uploaded. Upload URL: {upload_url}")
        return upload_url
    except requests.exceptions.RequestException as e:
        logging.error(f"AssemblyAI upload failed: {e}")
        raise

def assemblyai_transcribe(upload_url, language_code='en'):
    """Submits a transcription job to AssemblyAI."""
    url = 'https://api.assemblyai.com/v2/transcript'
    headers = {'authorization': ASSEMBLYAI_KEY, 'content-type': 'application/json'}
    body = {
        "audio_url": upload_url,
        "language_code": language_code,
        "punctuate": True,
        "format_text": True,
        "speaker_labels": False # Set to True if you want speaker diarization
    }
    logging.info(f"Submitting transcription job for URL: {upload_url} with language: {language_code}")
    try:
        resp = requests.post(url, headers=headers, json=body, timeout=30)
        resp.raise_for_status()
        transcript_id = resp.json()['id']
        logging.info(f"Transcription job submitted. Transcript ID: {transcript_id}")
        return transcript_id
    except requests.exceptions.RequestException as e:
        logging.error(f"AssemblyAI transcription submission failed: {e}")
        raise

def poll_transcript(transcript_id, timeout=300):
    """Polls AssemblyAI for transcription results."""
    url = f'https://api.assemblyai.com/v2/transcript/{transcript_id}'
    headers = {'authorization': ASSEMBLYAI_KEY}
    start_time = time.time()
    logging.info(f"Polling for transcription results for ID: {transcript_id}")
    while True:
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            status = data.get('status')
            if status == 'completed':
                logging.info("Transcription completed.")
                return data
            if status == 'error':
                error_msg = data.get('error', 'Unknown transcription error')
                logging.error(f"Transcription failed: {error_msg}")
                raise RuntimeError(f"Transcription failed: {error_msg}")
            if time.time() - start_time > timeout:
                logging.error("Transcription polling timed out after %s seconds.", timeout)
                raise TimeoutError("Transcription timed out")
            logging.info(f"Transcription status: {status}. Waiting...")
            time.sleep(2)
        except requests.exceptions.RequestException as e:
            logging.error(f"Error polling transcript {transcript_id}: {e}")
            if time.time() - start_time > timeout:
                raise TimeoutError("Transcription polling timed out due to network errors")
            time.sleep(5) # Wait longer on network errors

# ----------------- Gentle forced-alignment helpers -----------------
def gentle_align(audio_path, transcript_text, gentle_url=GENTLE_URL, timeout=120):
    """
    Sends audio + transcript to Gentle for forced alignment.
    Gentle endpoint: POST {gentle_url}/transcriptions?async=false
    """
    endpoint = f"{gentle_url}/transcriptions?async=false"
    logging.info(f"Sending audio '{audio_path}' and transcript to Gentle at {gentle_url}")
    try:
        with open(audio_path, 'rb') as f_audio:
            files = {
                'audio': ('audio.wav', f_audio, 'audio/wav'),
                'transcript': (None, transcript_text)
            }
            resp = requests.post(endpoint, files=files, timeout=timeout)
            resp.raise_for_status() # Raise an exception for bad status codes
            logging.info("Gentle alignment successful.")
            return resp.json()
    except FileNotFoundError:
        logging.error(f"Audio file not found at: {audio_path}")
        return None
    except requests.exceptions.ConnectionError:
        logging.error(f"Failed to connect to Gentle at {gentle_url}. Is it running?")
        return None
    except requests.exceptions.Timeout:
        logging.error(f"Gentle alignment request timed out after {timeout} seconds.")
        return None
    except requests.exceptions.RequestException as e:
        logging.error(f"Gentle alignment request failed: {e}")
        if e.response is not None:
            logging.error(f"Gentle response status: {e.response.status_code}, text: {e.response.text[:500]}")
        return None
    except Exception as e: # Catch any other unexpected errors
        logging.error(f"An unexpected error occurred during Gentle alignment: {e}")
        return None

def build_segments_from_gentle(alignment_json, gap_ms=300, min_segment_words=1):
    """
    Builds phrase segments from Gentle alignment words.
    Returns segments list with 'text', 'start' (ms), 'end' (ms).
    """
    words = alignment_json.get('words', [])
    segments = []
    current_words = []
    seg_start_ms = None
    prev_end_ms = None

    for w in words:
        word_text = w.get('word', '').strip()
        # Skip words that couldn't be aligned (no start time)
        if 'start' not in w or w['start'] is None:
            continue

        w_start_ms = int(float(w['start']) * 1000)
        w_end_ms = int(float(w['end']) * 1000)

        if seg_start_ms is None:
            # Start of a new segment
            seg_start_ms = w_start_ms
            current_words = [word_text]
            prev_end_ms = w_end_ms
            continue

        # Calculate gap between current word and previous word's end
        gap = w_start_ms - prev_end_ms if prev_end_ms is not None else 0

        if gap > gap_ms:
            # Gap is too large, finalize previous segment
            seg_text = ' '.join(current_words).strip()
            if len(current_words) >= min_segment_words and seg_text:
                segments.append({'text': seg_text, 'start': seg_start_ms, 'end': prev_end_ms})
            # Start a new segment with the current word
            seg_start_ms = w_start_ms
            current_words = [word_text]
            prev_end_ms = w_end_ms
        else:
            # Word is part of the current segment
            current_words.append(word_text)
            prev_end_ms = w_end_ms # Update end time to the end of the current word

    # Add the last segment if it exists
    if current_words and seg_start_ms is not None:
        seg_text = ' '.join(current_words).strip()
        segments.append({'text': seg_text, 'start': seg_start_ms, 'end': prev_end_ms})

    # Fallback if no segments were created (e.g., very short audio or alignment issues)
    if not segments and words:
        full_text = ' '.join([w.get('word', '').strip() for w in words if w.get('word')]).strip()
        # Find the earliest start and latest end from aligned words
        aligned_words = [w for w in words if 'start' in w and w['start'] is not None]
        if aligned_words:
            start_ms = int(min(w['start'] for w in aligned_words) * 1000)
            end_ms = int(max(w['end'] for w in aligned_words) * 1000)
            if full_text:
                segments.append({'text': full_text, 'start': start_ms, 'end': end_ms})
        elif full_text: # If no aligned words but text exists, assume a single segment
            segments.append({'text': full_text, 'start': 0, 'end': 1000}) # Default to 1 second

    logging.info(f"Generated {len(segments)} segments from Gentle alignment.")
    return segments

# ----------------- TTS and audio timeline -----------------
async def generate_tts_edge(text, out_path, voice=TTS_VOICE_DEFAULT):
    """Generates speech using Edge TTS and saves it to a file."""
    logging.info(f"Generating TTS for text: '{text[:50]}...' to {out_path} using voice: {voice}")
    try:
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(out_path)
        logging.info(f"TTS generated successfully for {out_path}.")
    except Exception as e:
        logging.error(f"Edge TTS generation failed for '{text[:50]}...': {e}")
        raise

def build_timeline_audio(segments, total_duration_s, tts_files, output_wav_path, target_sample_rate=48000, target_channels=2):
    """
    Builds the final audio timeline by overlaying TTS segments onto a silent base.
    """
    total_ms = int(total_duration_s * 1000)
    logging.info(f"Building audio timeline for {total_duration_s:.2f}s (total {total_ms}ms) into {output_wav_path}")

    # Create a silent base audio segment
    base = AudioSegment.silent(duration=total_ms, frame_rate=target_sample_rate)
    base = base.set_channels(target_channels)

    for i, seg in enumerate(segments):
        if i >= len(tts_files) or not tts_files[i] or not os.path.exists(tts_files[i]):
            logging.warning(f"Skipping TTS overlay for segment {i}: TTS file missing or not generated.")
            continue

        tts_path = tts_files[i]
        start_ms = int(seg.get('start', 0)) # Ensure start_ms is an integer
        logging.debug(f"Overlaying TTS {tts_path} at {start_ms}ms for segment: '{seg.get('text', '')[:50]}...'")
        try:
            tts_audio = AudioSegment.from_file(tts_path)
            tts_audio = tts_audio.set_frame_rate(target_sample_rate).set_channels(target_channels)
            base = base.overlay(tts_audio, position=start_ms)
        except Exception as e:
            logging.error(f"Failed to overlay TTS audio '{tts_path}' at {start_ms}ms: {e}")

    try:
        base.export(output_wav_path, format="wav")
        logging.info(f"Final audio timeline saved to {output_wav_path}")
        return output_wav_path
    except Exception as e:
        logging.error(f"Failed to export final audio timeline to {output_wav_path}: {e}")
        raise

def merge_audio_video(video_path, audio_path, output_path):
    """Merges the dubbed audio with the original video."""
    ffmpeg_bin = ensure_ffmpeg()
    logging.info(f"Merging video '{video_path}' with audio '{audio_path}' into '{output_path}'")
    cmd = [ffmpeg_bin, "-y", "-i", video_path, "-i", audio_path,
           "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
           "-shortest", "-movflags", "+faststart", output_path]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        logging.info(f"FFmpeg merge process completed successfully.")
        return True
    except subprocess.CalledProcessError as e:
        logging.error(f"FFmpeg merge failed. Return code: {e.returncode}")
        logging.error(f"FFmpeg stderr: {e.stderr.decode(errors='ignore')}")
        return False
    except Exception as e:
        logging.error(f"An unexpected error occurred during FFmpeg merge: {e}")
        return False

# ----------------- Gemini translation (unchanged logic) -----------------
def send_gemini_translation(text, source_lang, target_lang):
    """Translates text using Gemini API."""
    prompt_text = (
        f"Translate ONLY the following text from {source_lang} into {target_lang}.\n\n"
        "Important: Output ONLY the translated text. Do NOT include any explanatory notes, headings, "
        "labels, or delimiters. Do NOT add anything other than the translation.\n\n"
        f"Text:\n{text}"
    )
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
    headers = {"Content-Type": "application/json", "X-goog-api-key": GEMINI_KEY}
    data = {"contents":[{"parts":[{"text": prompt_text}]}]}
    logging.info(f"Sending text to Gemini for translation ({source_lang} -> {target_lang}): '{text[:50]}...'")
    try:
        response = requests.post(url, headers=headers, json=data, timeout=60)
        response.raise_for_status() # Raise an exception for bad status codes
        body = response.json()
        translated = None
        if 'candidates' in body and isinstance(body['candidates'], list) and len(body['candidates']) > 0:
            candidate = body['candidates'][0]
            if 'content' in candidate and 'parts' in candidate['content'] and len(candidate['content']['parts']) > 0:
                translated = candidate['content']['parts'][0].get('text')

        if not translated:
            # Fallback if 'candidates' structure is not as expected
            if 'output' in body:
                translated = body['output']
            else:
                translated = json.dumps(body)[:3000] # Use JSON string as a last resort

        if isinstance(translated, str):
            # Clean up potential preamble text from Gemini
            for prefix in ["Here is your translation:", "Translation:", "Translated text:", "Output:"]:
                if translated.strip().startswith(prefix):
                    translated = translated.strip()[len(prefix):].strip()
            translated = translated.strip()

        if not translated:
            logging.warning("Gemini returned an empty translation.")
            return text # Return original text if translation is empty

        logging.info(f"Gemini translation successful: '{translated[:50]}...'")
        return translated

    except requests.exceptions.RequestException as e:
        logging.error(f"Gemini translation request failed: {e}")
        if e.response is not None:
            logging.error(f"Gemini response status: {e.response.status_code}, text: {e.response.text[:500]}")
        return None
    except Exception as e:
        logging.error(f"Error processing Gemini response: {e}")
        return None

# ----------------- Bot handlers -----------------
@bot.message_handler(commands=['start'])
def start_cmd(message):
    """Handles the /start command."""
    bot.send_message(message.chat.id, "Welcome! Send your video, and I'll translate and dub it for you. Please send a video file.")

@bot.message_handler(content_types=['video'])
def handle_video(message):
    """Handles incoming video messages."""
    try:
        file_info = bot.get_file(message.video.file_id)
        if not file_info or not file_info.file_path:
            bot.send_message(message.chat.id, "Couldn't get file info. Please try again.")
            return

        logging.info(f"Downloading video file_id: {message.video.file_id}")
        downloaded = bot.download_file(file_info.file_path)

        # Save the downloaded video to a temporary file
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_video:
            tmp_video.write(downloaded)
            video_path = tmp_video.name

        logging.info(f"Video downloaded to temporary path: {video_path}")

        # Check video size and duration
        valid, msg = check_video_size_duration(video_path)
        if not valid:
            bot.send_message(message.chat.id, f"Error: {msg}")
            os.remove(video_path) # Clean up the temporary file
            return

        user_id = message.from_user.id
        user_data[user_id] = {'video_path': video_path}

        # Send language selection buttons
        markup = types.InlineKeyboardMarkup(row_width=3) # Adjusted row_width for better layout
        buttons = [types.InlineKeyboardButton(text=lang, callback_data=f"src|{lang}") for lang in SOURCE_LANGS]
        markup.add(*buttons)
        bot.send_message(message.chat.id, "Select the **original language** spoken in the video:", reply_markup=markup)

    except Exception as e:
        logging.exception(f"Error handling video message from chat_id {message.chat.id}: {e}")
        bot.send_message(message.chat.id, "An error occurred while processing your video. Please try again.")

@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    """Handles inline button callbacks."""
    user_id = call.from_user.id
    data = call.data or ""
    chat_id = call.message.chat.id
    message_id = call.message.message_id

    try:
        if data.startswith("src|"):
            lang = data.split("|", 1)[1]
            if user_id not in user_data or 'video_path' not in user_data[user_id]:
                logging.warning(f"Callback received for user {user_id} without video data.")
                try: bot.answer_callback_query(call.id, "Please send a video first.")
                except: pass
                return

            user_data[user_id]['source_lang'] = lang
            logging.info(f"User {user_id} selected source language: {lang}")

            # Send dubbing language selection buttons
            markup = types.InlineKeyboardMarkup(row_width=3) # Adjusted row_width
            buttons = [types.InlineKeyboardButton(text=lang2, callback_data=f"dub|{lang2}") for lang2 in DUB_LANGS]
            markup.add(*buttons)

            # Edit the message to show the next selection
            try:
                bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=markup)
                bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=f"Original language: **{lang}**. \nSelect the **dubbing language**:")
            except telebot.apihelper.ApiTelegramException as e:
                if "message is not modified" not in str(e): # Ignore if no change
                    logging.error(f"Error editing message for language selection: {e}")
                else:
                    logging.info("Message not modified, likely same selection.")

            try:
                bot.answer_callback_query(call.id)
            except Exception as e:
                logging.warning(f"Could not answer callback query: {e}")

        elif data.startswith("dub|"):
            lang = data.split("|", 1)[1]
            if user_id not in user_data or 'source_lang' not in user_data[user_id]:
                logging.warning(f"Callback received for user {user_id} without source language set.")
                try: bot.answer_callback_query(call.id, "Please select the original language first.")
                except: pass
                return

            user_data[user_id]['dub_lang'] = lang
            logging.info(f"User {user_id} selected dubbing language: {lang}")

            # Indicate processing and store message ID for updates
            try:
                bot.edit_message_text(chat_id=chat_id, message_id=message_id, text="Processing your video... This may take a few minutes.")
                status_msg_id = message_id
                user_data[user_id]['status_msg'] = {'chat_id': chat_id, 'message_id': status_msg_id}
            except Exception as e:
                logging.error(f"Error updating message to 'Processing...': {e}")

            try:
                bot.answer_callback_query(call.id)
            except Exception as e:
                logging.warning(f"Could not answer callback query: {e}")

            # Start the asynchronous processing pipeline
            # Note: asyncio.run is used here for simplicity in a single-file script.
            # For production, a more robust async framework or a task queue would be preferred.
            asyncio.run(process_video(chat_id, user_data[user_id]))

    except Exception as e:
        logging.exception(f"Error in callback_query handler for user {user_id}: {e}")
        try:
            bot.send_message(chat_id, "An unexpected error occurred during callback processing.")
            bot.answer_callback_query(call.id, "An error occurred.")
        except Exception as inner_e:
            logging.error(f"Failed to send error message or answer callback: {inner_e}")


# ----------------- Reused helper from earlier -----------------
def check_video_size_duration(file_path):
    """Checks if the video file meets size and duration constraints."""
    max_size_bytes = 20 * 1024 * 1024  # 20 MB
    max_duration_seconds = 120  # 2 minutes
    try:
        size = os.path.getsize(file_path)
        with VideoFileClip(file_path) as clip:
            duration = clip.duration
        if size > max_size_bytes:
            return False, f"File size ({size / (1024*1024):.1f} MB) exceeds the {max_size_bytes / (1024*1024):.0f} MB limit."
        if duration > max_duration_seconds:
            return False, f"Video duration ({duration:.1f}s) exceeds the {max_duration_seconds}s limit."
        return True, ""
    except Exception as e:
        logging.error(f"Error checking video properties for {file_path}: {e}")
        return False, "Could not verify video properties. Please ensure it's a valid video file."

# ----------------- Core processing pipeline (Gentle-integrated) -----------------
async def process_video(chat_id, data):
    """
    Main pipeline for video processing:
    1. Extract audio
    2. Transcribe audio using AssemblyAI
    3. Perform forced alignment using Gentle
    4. Translate segments using Gemini
    5. Generate TTS for translated segments
    6. Build final audio timeline
    7. Merge audio and video
    8. Send the result back to the user
    """
    video_path = data.get('video_path')
    source_lang = data.get('source_lang')
    dub_lang = data.get('dub_lang')
    status_info = data.get('status_msg')
    
    audio_wav_path = None
    final_audio_wav_path = None
    tts_output_paths = []
    output_video_path = f'dubbed_{chat_id}.mp4'
    
    # Temporary file cleanup function
    def cleanup_files(*paths):
        for p in paths:
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                    logging.debug(f"Cleaned up temporary file: {p}")
                except OSError as e:
                    logging.error(f"Error removing temporary file {p}: {e}")

    try:
        # --- Step 1: Extract audio ---
        if status_info:
            bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text="Processing: Extracting audio...")
        audio_wav_path = extract_audio_to_wav(video_path)

        # --- Step 2: Transcribe audio ---
        if status_info:
            bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text="Processing: Uploading audio for transcription...")
        upload_url = assemblyai_upload(audio_wav_path)

        if status_info:
            bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text="Processing: Submitting transcription job...")
        trans_id = assemblyai_transcribe(upload_url, language_code=LANG_CODE_ASR.get(source_lang, 'en'))

        if status_info:
            bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text="Processing: Waiting for transcription results...")
        transcript_json = poll_transcript(trans_id, timeout=300)
        transcript_text = transcript_json.get('text', '').strip()
        if not transcript_text:
            raise ValueError("Transcription resulted in empty text.")

        # --- Step 3: Forced Alignment (Gentle) ---
        segments = []
        if status_info:
            bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text="Processing: Running forced-alignment (Gentle)...")
        align_json = gentle_align(audio_wav_path, transcript_text)

        if align_json:
            segments = build_segments_from_gentle(align_json, gap_ms=250)
            logging.info("Successfully used Gentle for segmentation.")
        else:
            # Fallback to simpler ASR segmentation if Gentle fails
            if status_info:
                bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text="Gentle alignment failed. Falling back to ASR segmentation...")
            segments = build_segments_from_asr(transcript_json)
            logging.warning("Gentle alignment failed. Falling back to ASR segmentation.")

        if not segments:
            raise ValueError("No speech segments could be extracted.")

        # --- Step 4 & 5: Translate and Generate TTS ---
        if status_info:
            bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text=f"Processing: Translating and generating speech for {len(segments)} segments...")

        for idx, seg in enumerate(segments):
            original_text = seg.get('text', '').strip()
            if not original_text:
                tts_output_paths.append(None) # Append None for empty segments
                continue

            # Translate using Gemini
            translated_text = send_gemini_translation(original_text, source_lang, dub_lang)
            if not translated_text or translated_text.strip() == "":
                logging.warning(f"Gemini translation failed or returned empty for segment {idx}. Using original text.")
                translated_text = original_text # Fallback to original text

            # Generate TTS for translated text
            tts_temp_file = tempfile.NamedTemporaryFile(suffix=f"_seg{idx}.mp3", delete=False)
            tts_temp_file.close()
            tts_output_paths.append(tts_temp_file.name)
            
            voice = TTS_VOICES.get(dub_lang, TTS_VOICE_DEFAULT)
            await generate_tts_edge(translated_text, tts_temp_file.name, voice=voice)

            # Update progress message periodically
            if status_info and (idx + 1) % 5 == 0: # Update every 5 segments
                try:
                    bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text=f"Processing: Generating speech {idx + 1}/{len(segments)}...")
                except telebot.apihelper.ApiTelegramException as e:
                    if "message is not modified" not in str(e):
                        logging.warning(f"Failed to update progress message: {e}")

        # --- Step 6: Build final audio timeline ---
        if status_info:
            bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text="Processing: Building final audio track...")
        
        # Get total duration from the original video clip
        with VideoFileClip(video_path) as clip:
            total_duration_s = clip.duration

        final_audio_wav_path = tempfile.NamedTemporaryFile(suffix="_final.wav", delete=False).name
        build_timeline_audio(segments, total_duration_s, tts_output_paths, final_audio_wav_path)

        # --- Step 7: Merge audio and video ---
        if status_info:
            bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text="Processing: Merging audio and video...")
        
        if not merge_audio_video(video_path, final_audio_wav_path, output_video_path):
            raise RuntimeError("Failed to merge audio and video.")

        # --- Step 8: Send the result ---
        if status_info:
            bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text="Processing: Done! Sending your dubbed video...")
        else:
            bot.send_message(chat_id, "Your dubbed video is ready! Sending now...")

        with open(output_video_path, 'rb') as vf:
            bot.send_video(chat_id, vf, supports_streaming=True, caption="Your dubbed video!")

    except (ValueError, RuntimeError, TimeoutError, requests.exceptions.RequestException, subprocess.CalledProcessError) as e:
        logging.error(f"Processing pipeline error for chat_id {chat_id}: {e}")
        error_message = f"An error occurred during processing: {e}. Please try again."
        if status_info:
            try:
                bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text="Processing failed.")
            except: pass # Ignore errors if message is already deleted/not found
            bot.send_message(chat_id, error_message)
        else:
            bot.send_message(chat_id, error_message)

    except Exception as e:
        logging.exception(f"An unexpected error occurred in process_video for chat_id {chat_id}: {e}")
        error_message = "An unexpected internal error occurred. Please try again later."
        if status_info:
            try:
                bot.edit_message_text(chat_id=status_info['chat_id'], message_id=status_info['message_id'], text="An internal error occurred.")
            except: pass
            bot.send_message(chat_id, error_message)
        else:
            bot.send_message(chat_id, error_message)

    finally:
        # --- Cleanup ---
        logging.info(f"Starting cleanup for chat_id {chat_id}...")
        cleanup_files(audio_wav_path, final_audio_wav_path, output_video_path)
        for f in tts_output_paths:
            cleanup_files(f)
        # Remove user data after processing is complete or failed
        if chat_id in user_data:
            del user_data[chat_id]
            logging.info(f"Removed user data for chat_id {chat_id}.")
        logging.info(f"Cleanup finished for chat_id {chat_id}.")


# ----------------- Fallback ASR segments builder (simple) -----------------
def build_segments_from_asr(transcript_json, gap_ms=400):
    """
    Builds segments from AssemblyAI transcription JSON.
    Prioritizes 'utterances' if available, otherwise groups words by gaps.
    """
    segments = []
    words = transcript_json.get('words') or []

    # Prefer 'utterances' if they exist and are not empty
    if 'utterances' in transcript_json and transcript_json['utterances']:
        logging.info("Using 'utterances' from ASR results.")
        for u in transcript_json['utterances']:
            start = u.get('start', 0)
            end = u.get('end', 0)
            segments.append({'text': u.get('text', '').strip(), 'start': int(start * 1000), 'end': int(end * 1000)})
        if segments:
            return segments

    # Fallback to grouping words if no utterances or utterances are empty
    if not words:
        # If no words at all, create a single segment from the full text if available
        full_text = transcript_json.get('text', '').strip()
        audio_duration_ms = int(transcript_json.get('audio_duration', 0) * 1000)
        if full_text:
            segments.append({'text': full_text, 'start': 0, 'end': audio_duration_ms or 1000}) # Use duration or default 1s
        logging.warning("No words or utterances found in ASR result. Creating single segment if text exists.")
        return segments

    logging.info("Grouping words based on gaps for ASR segmentation.")
    current_group = []
    seg_start_ms = None
    prev_end_ms = None

    for w in words:
        word_text = w.get('text', '').strip()
        w_start = w.get('start')
        w_end = w.get('end')

        if w_start is None or w_end is None:
            # Skip words without timing info
            continue
            
        w_start_ms = int(w_start * 1000)
        w_end_ms = int(w_end * 1000)

        if seg_start_ms is None:
            # First word, start a new segment
            seg_start_ms = w_start_ms
            current_group = [word_text]
            prev_end_ms = w_end_ms
            continue

        # Calculate gap between current word start and previous word end
        gap = w_start_ms - prev_end_ms if prev_end_ms is not None else 0

        if gap > gap_ms:
            # Gap is too large, finalize the current segment
            seg_text = ' '.join(current_group).strip()
            if seg_text:
                segments.append({'text': seg_text, 'start': seg_start_ms, 'end': prev_end_ms})
            # Start a new segment with the current word
            seg_start_ms = w_start_ms
            current_group = [word_text]
            prev_end_ms = w_end_ms
        else:
            # Word belongs to the current segment
            current_group.append(word_text)
            prev_end_ms = w_end_ms # Update end time to the end of the current word

    # Add the last segment if there are remaining words
    if current_group and seg_start_ms is not None:
        seg_text = ' '.join(current_group).strip()
        segments.append({'text': seg_text, 'start': seg_start_ms, 'end': prev_end_ms})

    logging.info(f"Generated {len(segments)} segments from ASR word grouping.")
    return segments

# ----------------- Webhook handler and setup -----------------
@app.route('/', methods=['POST'])
def webhook_handler():
    """Handles incoming updates from Telegram."""
    if request.method == 'POST':
        try:
            json_str = request.get_data().decode('utf-8')
            update = telebot.types.Update.de_json(json_str)
            bot.process_new_updates([update])
            logging.info("Processed incoming webhook update.")
        except Exception as e:
            logging.exception(f"Failed to process incoming webhook: {e}")
    return '', 200

def setup_webhook():
    """Sets up the webhook for receiving Telegram updates."""
    if not WEBHOOK_URL:
        logging.warning("WEBHOOK_URL is not set. Bot will run in polling mode (if Flask is not used to serve).")
        # If not using Flask to serve, you might want bot.polling() here.
        # With Flask, the webhook handles incoming messages.
        return
    try:
        # It's good practice to remove any existing webhook first
        # bot.remove_webhook() # Uncomment if you experience issues with webhook registration
        
        # Ensure the webhook URL is correct and accessible from Telegram servers.
        # For local testing, ngrok or similar is needed. For deployment, use your service URL.
        logging.info(f"Attempting to set webhook to: {WEBHOOK_URL}")
        success = bot.set_webhook(WEBHOOK_URL)
        
        if not success:
            logging.error(f"Failed to set webhook to {WEBHOOK_URL}. Check URL validity and permissions.")
        else:
            logging.info(f"Webhook successfully set to {WEBHOOK_URL}")
    except telebot.apihelper.ApiTelegramException as e:
        logging.error(f"Telegram API error setting webhook: {e.description}")
    except Exception as e:
        logging.exception(f"An error occurred while setting up the webhook: {e}")

# ----------------- Main Execution Block -----------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    print("Starting Telegram dubbing bot...")
    
    # Setup webhook only if WEBHOOK_URL is provided and Flask is used for serving.
    # If running purely as a script without Flask, you'd use bot.polling() instead.
    if WEBHOOK_URL and not WEBHOOK_URL.startswith("http://localhost"): # Avoid setting webhook for local dev if not using ngrok
        setup_webhook()
    else:
        logging.warning("Webhook setup skipped. Ensure your deployment platform handles incoming requests or run bot.polling() if not using Flask.")

    # Run the Flask app to listen for webhook requests
    port = int(os.environ.get("PORT", 5000))
    logging.info(f"Starting Flask server on port {port}")
    # Note: For local development, you might want to run `bot.polling()` in a separate thread
    # or manage it differently if not using a public webhook URL with Flask.
    app.run(host="0.0.0.0", port=port, debug=False) # Set debug=True for local development if needed

