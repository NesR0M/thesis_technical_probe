import os
import subprocess
import signal
import time
import threading
import RPi.GPIO as GPIO
from gpiozero import Button
from signal import pause
import openai
from dotenv import load_dotenv
from elevenlabs.client import ElevenLabs
import logging
from logging.handlers import RotatingFileHandler
from sdnotify import SystemdNotifier

# -------------------- Konfiguration --------------------
load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")
elevenlabs = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))

TRIG = 4
ECHO = 17
BUTTON_GPIO = 22
DISTANCE_THRESHOLD = 10
DELAY_SECONDS = 12 * 60  # Zeit bis Erinnerung
CANCEL_SECONDS = 60       # Unterbrechung erlaubt
RECORD_SECONDS = 20
FILENAME = "aufnahme.wav"
AUDIO_OUTPUT = "response.wav"

# -------------------- Logging --------------------
logger = logging.getLogger("ProbeLogger")
logger.setLevel(logging.DEBUG)

# Datei-Log
file_handler = RotatingFileHandler("probe.log", maxBytes=1000000, backupCount=3)
logger.addHandler(file_handler)

# → Neu: stdout-Log
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)
logger.addHandler(console_handler)

# -------------------- Setup --------------------
GPIO.setmode(GPIO.BCM)
GPIO.setup(TRIG, GPIO.OUT)
GPIO.setup(ECHO, GPIO.IN)
button = Button(BUTTON_GPIO, pull_up=True, bounce_time=0.1)
notifier = SystemdNotifier()

# -------------------- Globale Zustände --------------------
recording_process = None
is_recording = False
recording_start_time = None
latest_audio_file = None
latest_text_prompt = None
reflection_prompt_played = False
last_activity_time = time.time()
processing_lock = threading.Lock()

# -------------------- Helferfunktion für sichere Threads --------------------
def safe_thread(target, *args):
    def wrapper():
        try:
            target(*args)
        except Exception as e:
            logger.exception(f"Thread {target.__name__} crashed")
    t = threading.Thread(target=wrapper)
    t.daemon = True
    t.start()

# -------------------- Retry-Helferfunktion --------------------
def retry(func, max_retries=2, delay=2, exceptions=(Exception,)):
    for attempt in range(max_retries):
        try:
            return func()
        except exceptions as e:
            if attempt < max_retries - 1:
                logger.warning(f"Fehler bei {func.__name__} (Versuch {attempt+1}): {e}")
                time.sleep(delay)
            else:
                logger.error(f"Fehlgeschlagen nach {max_retries} Versuchen: {e}")
                raise

# -------------------- Funktionen --------------------
def measure_distance():
    global last_activity_time
    try:
        GPIO.output(TRIG, False)
        time.sleep(0.02)
        GPIO.output(TRIG, True)
        time.sleep(0.00001)
        GPIO.output(TRIG, False)

        start = time.time()
        timeout = start + 0.05
        while GPIO.input(ECHO) == 0:
            if time.time() > timeout:
                raise TimeoutError("Timeout beim Warten auf Echo (start)")
        start = time.time()

        timeout = start + 0.05
        while GPIO.input(ECHO) == 1:
            if time.time() > timeout:
                raise TimeoutError("Timeout beim Warten auf Echo (stop)")
        stop = time.time()

        elapsed = stop - start
        last_activity_time = time.time()
        return (elapsed * 34300) / 2

    except Exception as e:
        logger.warning("Fehler bei der Distanzmessung: %s", e)
        return 0

def start_recording():
    global recording_process, is_recording, recording_start_time, last_activity_time
    if is_recording:
        return
    logger.info("Start recording...")
    recording_start_time = time.time()
    last_activity_time = recording_start_time
    try:
        recording_process = subprocess.Popen([
            "/usr/bin/arecord", "-D", "plughw:1",
            "-f", "cd", "-t", "wav",
            "-r", "16000", "-d", str(RECORD_SECONDS), FILENAME
        ])
        is_recording = True
        safe_thread(wait_and_stop_recording)
    except Exception as e:
        logger.exception("Fehler beim Starten der Aufnahme")
        is_recording = False

def wait_and_stop_recording():
    global recording_process
    recording_process.wait()
    if is_recording:
        logger.info("Recording automatically stopped after 20 seconds!")
        stop_recording()


def stop_recording():
    global recording_process, is_recording, latest_audio_file, last_activity_time
    if not is_recording or not recording_process:
        return  # Avoid stopping if nothing is recording

    logger.info("Stop recording.")
    try:
        recording_process.terminate()
        recording_process.wait()
    except Exception as e:
        logger.warning("Problem beim Beenden des Aufnahmeprozesses: %s", e)

    duration = time.time() - recording_start_time
    is_recording = False
    last_activity_time = time.time()

    if duration < 1:
        logger.info("Aufnahme zu kurz. Verwerfe Datei.")
        try:
            os.remove(FILENAME)
        except OSError:
            pass
        return

    logger.info(f"Gespeichert: {FILENAME} ({duration:.2f}s)")
    safe_thread(process_recording, FILENAME)


def process_recording(filename):
    global latest_audio_file, latest_text_prompt
    with processing_lock:
        try:
            logger.info("Transkribiere über Whisper...")
            def do_transcribe():
                with open(filename, "rb") as audio_file:
                    return openai.audio.transcriptions.create(
                        model="whisper-1", file=audio_file
                    )
            whisper_resp = retry(do_transcribe)
            transkription = whisper_resp.text
            logger.info("Transkription: %s", transkription)

            logger.info("Sende an GPT-4...")
            def do_chat():
                return openai.chat.completions.create(
                    model="gpt-4",
                    messages=[
                        {"role": "system", "content": "You are a supportive and encouraging assistant helping someone follow through on an offline activity they intended to do after using their phone. Your message should always consist of two sentences: Start with a warm and friendly check-in, such as: Hey, I noticed your phone is still out. or Hi there, just checking in—remember what you told me before? In the second sentence, restate their planned activity vividly, highlight the sensory or emotional reward, and end with an open, reflective question. Examples: Can you picture how nice it will feel to have the dishes done—where would you start? Imagine the fresh air on your face during your walk—what's the first step? Keep the tone friendly, non-judgmental, and gently encouraging. Avoid commands. The planned activity was:"},
                        {"role": "user", "content": transkription}
                    ]
                )
            chat_resp = retry(do_chat)
            antwort = chat_resp.choices[0].message.content
            latest_text_prompt = antwort
            logger.info("GPT-4: %s", antwort)

            logger.info("Erzeuge Sprachausgabe...")
            def do_tts():
                return elevenlabs.text_to_speech.convert(
                    text=antwort,
                    voice_id="FTNCalFNG5bRnkkaP5Ug",
                    model_id="eleven_multilingual_v2",
                    output_format="pcm_16000"
                )
            audio = retry(do_tts)

            with open(AUDIO_OUTPUT, "wb") as f:
                for chunk in audio:
                    f.write(chunk)

            latest_audio_file = AUDIO_OUTPUT
            logger.info("Audio gespeichert: %s", AUDIO_OUTPUT)
        except Exception as e:
            logger.exception("Fehler bei der Verarbeitung")

def play_audio(file):
    logger.info("Reminder wird abgespielt.")
    os.system(f"/usr/bin/aplay -D 'plughw:sndrpihifiberry' -f S16_LE -r 16000 -c 1 {file}")

def distance_loop():
    global last_activity_time, reflection_prompt_played
    reminder_timer_started = False
    reminder_start_time = None
    pause_start_time = None

    while True:
        try:
            notifier.notify("WATCHDOG=1")
            dist = measure_distance()
            logger.info(f"Distance: {dist:.1f} cm")

            if dist > DISTANCE_THRESHOLD:
                if not reminder_timer_started:
                    logger.info("Bitte Aufnahme starten")
                    
                    if not reflection_prompt_played:
                        play_audio("pickup.wav")
                        reflection_prompt_played = True

                    if latest_audio_file:
                        reminder_start_time = time.time()
                        reminder_timer_started = True
                        logger.info("Reminder-Timer gestartet.")
                elif reminder_timer_started:
                    elapsed = time.time() - reminder_start_time
                    logger.info(f"Reminder läuft seit {int(elapsed)} Sekunden")
                    if elapsed >= DELAY_SECONDS:
                        safe_thread(play_audio, latest_audio_file)
                        reminder_timer_started = False 
                        reminder_start_time = None
                        logger.info("Reminder-Timer zurückgesetzt.")
                pause_start_time = None
            else:
                if reminder_timer_started:
                    if not pause_start_time:
                        pause_start_time = time.time()
                        logger.info("Handy erkannt – Timer pausiert.")
                    else:
                        paused_elapsed = time.time() - pause_start_time
                        logger.info(f"Handy liegt seit {int(paused_elapsed)}s wieder im Kasten")
                        if paused_elapsed >= CANCEL_SECONDS:
                            logger.info("Reminder abgebrochen – Handy wurde zurückgelegt.")
                            reminder_timer_started = False
                            reminder_start_time = None
                            pause_start_time = None
                            reflection_prompt_played = False
                else:
                    pause_start_time = None

            time.sleep(1)

        except Exception as e:
            logger.exception("Fehler in distance_loop")
            time.sleep(2)

# -------------------- Button --------------------
button.when_pressed = start_recording
button.when_released = stop_recording

# -------------------- Main --------------------
try:
    notifier.notify("READY=1")
    notifier.notify("WATCHDOG=1")
    play_audio("start.wav")
    distance_loop()
except KeyboardInterrupt:
    logger.info("Beende...")
    play_audio("stop.wav")
    GPIO.cleanup()
    if recording_process:
        recording_process.terminate()
        recording_process.wait()
    logger.info("Programm erfolgreich beendet.")
