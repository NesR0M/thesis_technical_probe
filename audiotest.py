import os
import time
import subprocess

RAW_PCM_FILE = "response.wav"               # Eigentlich kein echtes WAV!
WAV_CONVERTED = "response_converted.wav"    # Enthält echten RIFF-Header
BOOSTED_FILE = "boosted_response.wav"
SPEAKER_DEVICE = "plughw:0"

def convert_pcm_to_wav(input_file, output_file):
    print("Konvertiere PCM → WAV...")
    subprocess.run([
        "sox", "-t", "raw",
        "-r", "16000", "-e", "signed", "-b", "16", "-c", "1",
        input_file, output_file
    ])
    print("Konvertierung abgeschlossen.")

def boost_volume(input_file, output_file, gain_db="+6"):
    print("Booste Lautstärke mit stärkerem Fade...")
    subprocess.run([
        "sox", input_file, output_file,
        "gain", gain_db,
        "fade", "t", "1", "0", "0.2",  # fade in 1s, fade out 0.2s
        "pad", "0.5", "0.5"           # 50ms silence before and after
    ])
    print("Boost + Fade abgeschlossen.")

def play_audio(file):
    print("Spiele Audio ab...")
    os.system(f"aplay -D {SPEAKER_DEVICE} -f S16_LE -r 16000 -c 1 {file}")

try:
    while True:
        if os.path.exists(RAW_PCM_FILE):
            if not os.path.exists(BOOSTED_FILE):
                convert_pcm_to_wav(RAW_PCM_FILE, WAV_CONVERTED)
                boost_volume(WAV_CONVERTED, BOOSTED_FILE)
            play_audio(BOOSTED_FILE)
        else:
            print(f"{RAW_PCM_FILE} nicht gefunden. Warte...")
        time.sleep(10)
except KeyboardInterrupt:
    print("Beendet.")
