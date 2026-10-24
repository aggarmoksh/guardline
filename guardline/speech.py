"""Speech adapter: Cartesia (primary) and Deepgram (fallback). Chosen by SPEECH_PROVIDER in .env.

Audio in is 16 kHz mono 16-bit WAV (the browser converts before upload); audio out is WAV.
"""
import os
from typing import Protocol

import httpx
from dotenv import load_dotenv

# Cartesia voices (from the voice list): Skylar (en, customer care), Fernanda (es-MX, customer care).
CARTESIA_VOICES = {"en": "db6b0ed5-d5d3-463d-ae85-518a07d3c2b4", "es": "b4b8e2af-6139-466e-a93a-30c20d2e1fc5"}
DEEPGRAM_VOICES = {"en": "aura-2-thalia-en", "es": "aura-2-celeste-es"}


class SpeechError(Exception):
    pass


class Speech(Protocol):
    name: str

    def transcribe(self, wav: bytes, language: str) -> str: ...
    def synthesize(self, text: str, language: str) -> bytes: ...


class CartesiaSpeech:
    name = "cartesia"
    STT_MODEL = "ink-whisper"
    TTS_MODEL = "sonic-3"

    def __init__(self):
        from cartesia import Cartesia

        self._c = Cartesia(api_key=os.environ["CARTESIA_API_KEY"], timeout=30.0, max_retries=1)

    def transcribe(self, wav: bytes, language: str) -> str:
        try:
            r = self._c.stt.transcribe(file=("audio.wav", wav, "audio/wav"), model=self.STT_MODEL, language=language)
        except Exception as e:
            raise SpeechError(f"cartesia stt: {type(e).__name__}") from None
        return (r.text or "").strip()

    def synthesize(self, text: str, language: str) -> bytes:
        try:
            r = self._c.tts.generate(
                model_id=self.TTS_MODEL, transcript=text, voice={"id": CARTESIA_VOICES[language]},
                output_format={"container": "wav", "encoding": "pcm_s16le", "sample_rate": 24000},
                language=language)
            return r.read()
        except Exception as e:
            raise SpeechError(f"cartesia tts: {type(e).__name__}") from None


class DeepgramSpeech:
    name = "deepgram"
    STT_MODEL = "nova-3"
    BASE = "https://api.deepgram.com/v1"

    def __init__(self):
        self._h = {"Authorization": f"Token {os.environ['DEEPGRAM_API_KEY']}"}
        self._http = httpx.Client(timeout=30.0)

    def transcribe(self, wav: bytes, language: str) -> str:
        try:
            r = self._http.post(f"{self.BASE}/listen", params={"model": self.STT_MODEL, "language": language,
                                "smart_format": "true"}, headers={**self._h, "Content-Type": "audio/wav"}, content=wav)
            r.raise_for_status()
            return r.json()["results"]["channels"][0]["alternatives"][0]["transcript"].strip()
        except Exception as e:
            raise SpeechError(f"deepgram stt: {type(e).__name__}") from None

    def synthesize(self, text: str, language: str) -> bytes:
        try:
            r = self._http.post(f"{self.BASE}/speak", params={"model": DEEPGRAM_VOICES[language],
                                "encoding": "linear16", "container": "wav"}, headers=self._h, json={"text": text})
            r.raise_for_status()
            return r.content
        except Exception as e:
            raise SpeechError(f"deepgram tts: {type(e).__name__}") from None


def get_speech() -> Speech:
    load_dotenv()
    provider = os.environ.get("SPEECH_PROVIDER", "cartesia").lower()
    if provider == "cartesia":
        return CartesiaSpeech()
    if provider == "deepgram":
        return DeepgramSpeech()
    raise SpeechError(f"unknown SPEECH_PROVIDER: {provider}")
