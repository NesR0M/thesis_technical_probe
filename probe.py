import os
import re
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
DELAY_SECONDS = 10 * 60  # Zeit bis Erinnerung
CANCEL_SECONDS = 180       # Unterbrechung erlaubt
RECORD_SECONDS = 20
FILENAME = "aufnahme.wav"
AUDIO_OUTPUT = "response.wav"

# -------------------- Logging --------------------
logger = logging.getLogger("ProbeLogger")
logger.setLevel(logging.DEBUG)

# Datei-Log
file_handler = RotatingFileHandler("/var/log/probe/probe.log", maxBytes=1000000, backupCount=3)
logger.addHandler(file_handler)


# Zusätzlicher Logger für Study-Einträge
study_logger = logging.getLogger("StudyLogger")
study_logger.setLevel(logging.INFO)

study_handler = RotatingFileHandler("/var/log/probe/study.log", maxBytes=1000000, backupCount=10)
formatter = logging.Formatter('%(asctime)s - %(message)s')

study_handler.setFormatter(formatter)
study_logger.addHandler(study_handler)

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

def find_recording_device(name_hint="USB PnP Sound Device"):
    try:
        output = subprocess.check_output(["/usr/bin/arecord", "-l"], text=True)
        # Suche Zeile mit passendem Gerätenamen
        for line in output.splitlines():
            if name_hint in line:
                match = re.search(r"card (\d+):.*device (\d+):", line)
                if match:
                    card_index = match.group(1)
                    device_index = match.group(2)
                    return f"plughw:{card_index},{device_index}"
    except Exception as e:
        logger.warning("Konnte Aufnahmegerät nicht finden: %s", e)
    return "plughw:0,0"  # fallback

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
            "/usr/bin/arecord", "-D", find_recording_device(),
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
    play_audio("sounds/feedback_fast.wav")
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
            study_logger.info("Transkription: %s", transkription)

            logger.info("Sende an GPT-4...")
            def do_chat():
                return openai.chat.completions.create(
                    model="gpt-4",
                    messages=[
                        {"role": "system", "content": "You are a supportive and encouraging assistant helping someone follow through on an offline activity they intended to do after using their phone. Your response should always be two sentences: Start with a warm, friendly check-in that gently reminds the user their phone is still out of the box (this doesn't mean they're using it). Example: Hey, I noticed you haven’t put your phone back yet. Hi there, just checking in—remember what you told me before? Restate their planned activity vividly, using sensory or emotional language. Highlight a possible reward or positive feeling, and end with an open-ended, reflective question (not a command). Examples: Can you picture how nice it will feel to have the dishes done—what would you have to do first to start? Imagine the fresh air on your face during your walk—where would you like to go? Keep the tone friendly, non-judgmental, gently encouraging, and reflective. Avoid direct instructions or pressure. The planned activity is:"},
                        {"role": "user", "content": transkription}
                    ]
                )
            chat_resp = retry(do_chat)
            antwort = chat_resp.choices[0].message.content
            latest_text_prompt = antwort
            logger.info("GPT-4: %s", antwort)
            study_logger.info("GPT-4: %s", antwort)

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
    inbox_start_time = None
    outbox_start_time = None
        
    # Neue Variablen für Stabilitätslogik
    box_state = "out"               # Aktueller stabiler Zustand: "in" oder "out"
    pending_state = "out"           # Neuer potenzieller Zustand
    pending_state_start = time.time()     # Zeit, seit der dieser potenzielle Zustand anhält
    STABILITY_SECONDS = 2          # Schwelle für stabile Änderung

    while True:
        try:
            notifier.notify("WATCHDOG=1")
            dist = measure_distance()
            logger.info(f"Distance: {dist:.1f} cm")

            current_raw_state = "out" if dist > DISTANCE_THRESHOLD else "in"
            now = time.time()

            # Wenn Zustand wechselt, aber noch nicht lange genug
            if current_raw_state != box_state:
                if pending_state != current_raw_state:
                    pending_state = current_raw_state
                    pending_state_start = now
                elif now - pending_state_start >= STABILITY_SECONDS:
                    # Zustand ist jetzt stabil → übernehmen
                    box_state = pending_state
                    if box_state == "in":
                        logger.info("Handy ist in Box.")
                        inbox_start_time = now
                        if outbox_start_time:
                            study_logger.info(f"Phone out Box: {outbox_start_time}; Dauer: {inbox_start_time - outbox_start_time:.2f}s; Ende: {inbox_start_time}")
                            outbox_start_time = None
                    else:
                        logger.info("Kein Handy in Box.")
                        outbox_start_time = now
                        if inbox_start_time:
                            study_logger.info(f"Phone in Box: {inbox_start_time}; Dauer: {outbox_start_time - inbox_start_time:.2f}s; Ende: {outbox_start_time}")
                            inbox_start_time = None
            else:
                pending_state = None
                pending_state_start = None

            # Reminder-Logik wie gehabt
            if box_state == "out":
                if not reminder_timer_started:
                    if not reflection_prompt_played:
                        play_audio("pickup.wav")
                        logger.info("Bitte Aufnahme starten")
                        reflection_prompt_played = True

                    if latest_audio_file:
                        reminder_start_time = now
                        reminder_timer_started = True
                        logger.info("Reminder-Timer gestartet.")
                elif reminder_timer_started:
                    elapsed = now - reminder_start_time
                    logger.info(f"Reminder läuft seit {int(elapsed)} Sekunden")
                    if elapsed >= DELAY_SECONDS:
                        safe_thread(play_audio, latest_audio_file)
                        reminder_timer_started = False 
                        reminder_start_time = None
                        logger.info("Reminder wird abgespielt und zurückgesetzt.")
            elif box_state == "in":
                if inbox_start_time:
                    inbox_duration = now - inbox_start_time
                    logger.info(f"Handy liegt seit {int(inbox_duration)}s im Kasten")
                    if inbox_duration >= CANCEL_SECONDS:
                        if reminder_timer_started:
                            logger.info("Reminder abgebrochen.")
                            reminder_timer_started = False
                            reminder_start_time = None
                        logger.info("reflextion notification active.")  
                        reflection_prompt_played = False

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
