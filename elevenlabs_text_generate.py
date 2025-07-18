import os
from elevenlabs.client import ElevenLabs
from dotenv import load_dotenv

# Load your ElevenLabs API key from .env
load_dotenv()
api_key = os.getenv("ELEVENLABS_API_KEY")

# Initialize ElevenLabs client
client = ElevenLabs(api_key=api_key)

# Your custom startup message
text = "The device is shutting down."

# Generate speech
audio = client.text_to_speech.convert(
    text=text,
    voice_id="FTNCalFNG5bRnkkaP5Ug",  # Adjust if you want another voice
    model_id="eleven_multilingual_v2",
    output_format="pcm_16000"
)

# Save to pickup.wav
with open("stop.wav", "wb") as f:
    for chunk in audio:
        f.write(chunk)

print("stop.wav created successfully.")
