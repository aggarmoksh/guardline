"""python -m guardline.health: one real call to each dependency, OK/FAIL per line. Prints no secrets or data."""
import io
import time
import wave

from dotenv import load_dotenv


def _silence_wav(ms: int = 600) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * (16 * ms))
    return buf.getvalue()


def _check_llm() -> None:
    from guardline.llm import LLM

    if not LLM().complete("Reply with the single word: ok", "ping", max_tokens=10):
        raise RuntimeError("empty reply")


def _check_crs() -> None:
    from guardline.crs import CRSClient

    CRSClient().login()


def _check_stt() -> None:
    from guardline.speech import get_speech

    get_speech().transcribe(_silence_wav(), "en")


def _check_tts() -> None:
    from guardline.speech import get_speech

    if len(get_speech().synthesize("Health check.", "en")) < 1000:
        raise RuntimeError("audio too short")


def main() -> int:
    load_dotenv()
    from guardline.speech import get_speech

    provider = get_speech().name
    failed = 0
    for name, fn in (("LLM", _check_llm), ("CRS login", _check_crs),
                     (f"STT ({provider})", _check_stt), (f"TTS ({provider})", _check_tts)):
        t0 = time.perf_counter()
        try:
            fn()
            print(f"OK   {name} ({(time.perf_counter() - t0) * 1000:.0f} ms)")
        except Exception as e:
            failed += 1
            print(f"FAIL {name}: {type(e).__name__}")
    return failed


if __name__ == "__main__":
    raise SystemExit(main())
