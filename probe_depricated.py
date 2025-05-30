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

# -------------------- Konfiguration --------------------
load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")
elevenlabs = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))

TRIG = 4
ECHO = 17
BUTTON_GPIO = 22
DISTANCE_THRESHOLD = 10
DELAY_SECONDS = 2 * 60  # Zeit bis Erinnerung
CANCEL_SECONDS = 60       # Unterbrechung erlaubt
FILENAME = "aufnahme.wav"
AUDIO_OUTPUT = "response.wav"
WATCHDOG_TIMEOUT = 30 * 60  # 30 Minuten Inaktivität

# -------------------- Setup --------------------
GPIO.setmode(GPIO.BCM)
GPIO.setup(TRIG, GPIO.OUT)
GPIO.setup(ECHO, GPIO.IN)
button = Button(BUTTON_GPIO, pull_up=True, bounce_time=0.1)

# -------------------- Globale Zustände --------------------
recording_process = None
is_recording = False
recording_start_time = None
latest_audio_file = None
latest_text_prompt = None
last_activity_time = time.time()
processing_lock = threading.Lock()

# -------------------- Funktionen --------------------
def measure_distance():
    global last_activity_time
    GPIO.output(TRIG, False)
    time.sleep(0.02)
    GPIO.output(TRIG, True)
    time.sleep(0.00001)
    GPIO.output(TRIG, False)
    start = time.time()
    while GPIO.input(ECHO) == 0:
        start = time.time()
    while GPIO.input(ECHO) == 1:
        stop = time.time()
    elapsed = stop - start
    last_activity_time = time.time()
    return (elapsed * 34300) / 2

def start_recording():
    global recording_process, is_recording, recording_start_time, last_activity_time
    if is_recording:
        return
    print("Start recording...")
    recording_start_time = time.time()
    last_activity_time = recording_start_time
    recording_process = subprocess.Popen([
        "arecord", "-D", "plughw:1",  # USB mic device
        "-f", "cd", "-t", "wav",
        "-r", "16000", FILENAME
    ])
    is_recording = True

def stop_recording():
    global recording_process, is_recording, latest_audio_file, last_activity_time
    if not recording_process:
        return
    print("Stop recording.")
    recording_process.terminate()
    recording_process.wait()
    duration = time.time() - recording_start_time
    is_recording = False
    last_activity_time = time.time()

    if duration < 1:
        print("Aufnahme zu kurz. Verwerfe Datei.")
        try:
            os.remove(FILENAME)
        except OSError:
            pass
        return

    print(f"Gespeichert: {FILENAME} ({duration:.2f}s)")
    threading.Thread(target=process_recording, args=(FILENAME,)).start()

def process_recording(filename):
    global latest_audio_file, latest_text_prompt
    with processing_lock:
        try:
            print("Transkribiere über Whisper...")
            with open(filename, "rb") as audio_file:
                whisper_resp = openai.audio.transcriptions.create(
                    model="whisper-1", file=audio_file
                )
            transkription = whisper_resp.text
            print("Transkription:", transkription)

            print("Sende an GPT-4...")
            chat_resp = openai.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": "You are a supportive and encouraging assistant helping someone follow through on an offline activity they intended to do after using their phone. Write a short, warm reminder."},
                    {"role": "user", "content": transkription}
                ]
            )
            antwort = chat_resp.choices[0].message.content
            latest_text_prompt = antwort
            print("GPT-4:", antwort)

            print("Erzeuge Sprachausgabe...")
            audio = elevenlabs.text_to_speech.convert(
                text=antwort,
                voice_id="JBFqnCBsd6RMkjVDRZzb",
                model_id="eleven_multilingual_v2",
                output_format="pcm_16000"
            )

            with open(AUDIO_OUTPUT, "wb") as f:
                for chunk in audio:
                    f.write(chunk)

            latest_audio_file = AUDIO_OUTPUT
            print("Audio gespeichert:", AUDIO_OUTPUT)
        except Exception as e:
            print("Fehler bei der Verarbeitung:", e)

def play_audio(file):
    print("Reminder wird abgespielt.")
    os.system(f"aplay -D plughw:0 -f S16_LE -r 16000 -c 1 {file}")

def distance_loop():
    global last_activity_time
    reminder_timer_started = False
    reminder_start_time = None
    pause_start_time = None

    while True:
        try:
            dist = measure_distance()
            print(f"Distance: {dist:.1f} cm")

            if dist > DISTANCE_THRESHOLD:
                if not reminder_timer_started:
                    print("Bitte Aufnahme starten")
                    if latest_audio_file:
                        reminder_start_time = time.time()
                        reminder_timer_started = True
                        print("Reminder-Timer gestartet.")
                elif reminder_timer_started:
                    elapsed = time.time() - reminder_start_time
                    print(f"Reminder läuft seit {int(elapsed)} Sekunden")
                    if elapsed >= DELAY_SECONDS:
                        threading.Thread(target=play_audio, args=(latest_audio_file,)).start()
                        reminder_timer_started = False 
                        reminder_start_time = None
                        print("Reminder-Timer zurückgesetzt.")
                pause_start_time = None
            else:
                if reminder_timer_started:
                    if not pause_start_time:
                        pause_start_time = time.time()
                        print("Handy erkannt – Timer pausiert.")
                    else:
                        paused_elapsed = time.time() - pause_start_time
                        print(f"Handy liegt seit {int(paused_elapsed)}s wieder im Kasten")
                        if paused_elapsed >= CANCEL_SECONDS:
                            print("Reminder abgebrochen – Handy wurde zurückgelegt.")
                            reminder_timer_started = False
                            reminder_start_time = None
                            pause_start_time = None
                else:
                    pause_start_time = None

            time.sleep(1)

        except Exception as e:
            print("Fehler in distance_loop:", e)
            time.sleep(2)

def watchdog_loop():
    global last_activity_time
    while True:
        time.sleep(10)
        if time.time() - last_activity_time > WATCHDOG_TIMEOUT:
            print("Keine Aktivität erkannt – führe Neustart durch...")
            os.system("sudo reboot")

# -------------------- Button --------------------
button.when_pressed = start_recording
button.when_released = stop_recording

# -------------------- Main --------------------
try:
    threading.Thread(target=watchdog_loop, daemon=True).start()
    distance_loop()
except KeyboardInterrupt:
    print("Beende...")
    GPIO.cleanup()
    if recording_process:
        recording_process.terminate()
        recording_process.wait()
    print("Programm erfolgreich beendet.")