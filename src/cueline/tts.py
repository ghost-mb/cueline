"""Text-to-speech for Studio podcasts. Provider is a setting (AI model dialog → Podcast voices):

- "kokoro" (default): Kokoro-82M via kokoro-onnx, on this computer, $0, Apache-2.0. The int8 model (~90 MB) and the
  voice pack (~27 MB) are downloaded once into data/models/kokoro/ on first use. Best in English.
- "openai": the saved OpenAI key, `audio.speech` (one call per turn, raw 24 kHz PCM).
- "gemini": the saved Gemini key, native multi-speaker TTS (REST, a few turns per call, raw 24 kHz PCM).

Every engine returns 24 kHz mono 16-bit PCM; `render()` joins the turns with short pauses and encodes one MP3.

    mp3_bytes, seconds = tts.render([{"speaker": "A", "text": "..."}, {"speaker": "B", "text": "..."}])
"""
import base64
import io
import json
import os
import threading
import urllib.error
import urllib.request
from pathlib import Path

from . import db
from . import llm
from .llm import LLMError, QuotaError

RATE = 24000
# Pace (2026-10-28: the first episode at speed 1.0 + 0.35 s turn pauses felt slow; the user listened at 1.25x).
PAUSE_S = 0.15  # silence between turns
SPEED = float(os.environ.get("CUELINE_VOICE_SPEED", "1.2"))  # Kokoro speed; ~200 spoken words per minute
PACE_NOTE = "Speak at a brisk, lively podcast pace, like two friends who know the topic well. No long pauses."
PROVIDERS = {
    "kokoro": {"label": "Kokoro (on this computer)", "note": "Free, runs on this computer. Best in English. First use downloads the voices (~120 MB)",
               "voices": {"A": "af_heart", "B": "am_michael"}},
    "openai": {"label": "OpenAI", "default": "gpt-4o-mini-tts", "note": "Billed to your OpenAI key per minute of audio",
               "voices": {"A": "coral", "B": "onyx"}},
    "gemini": {"label": "Gemini", "default": "gemini-2.5-flash-preview-tts", "note": "Billed to your Gemini key; natural two-voice conversations",
               "voices": {"A": "Kore", "B": "Puck"}},
}
KOKORO_DIR = db.DATA_DIR / "models" / "kokoro"
KOKORO_FILES = {  # thewh1teagle/kokoro-onnx release assets
    "kokoro-v1.0.int8.onnx": "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.int8.onnx",
    "voices-v1.0.bin": "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin",
}
GEMINI_CHUNK_CHARS = 3000  # turns per multi-speaker call stay under this

_lock = threading.Lock()  # one podcast at a time: CPU-heavy (Kokoro) or billed (APIs)
_kokoro = None


def choice() -> tuple[str, str]:
    provider = db.setting("tts_provider", "kokoro")
    if provider not in PROVIDERS:
        provider = "kokoro"
    return provider, db.setting(f"tts_model_{provider}", "") or PROVIDERS[provider].get("default", "")


def status() -> dict:
    provider, model = choice()
    return {"provider": provider, "label": PROVIDERS[provider]["label"], "model": model}


# ---------- engines: each returns PCM bytes (24 kHz, mono, s16le) for one turn or chunk ----------
def _download(url: str, dest: Path):
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Cueline"}), timeout=60) as res, \
                open(tmp, "wb") as out:
            while chunk := res.read(1 << 20):
                out.write(chunk)
    except (urllib.error.URLError, OSError) as exc:
        tmp.unlink(missing_ok=True)
        raise LLMError(f"Couldn't download the Kokoro voices ({exc}). Check the internet connection and try again.") from exc
    tmp.replace(dest)


def _get_kokoro():
    global _kokoro
    if _kokoro is None:
        from . import stt
        stt.need_audio("kokoro_onnx")  # before the ~90 MB model download
        KOKORO_DIR.mkdir(parents=True, exist_ok=True)
        for name, url in KOKORO_FILES.items():
            if not (KOKORO_DIR / name).is_file():
                _download(url, KOKORO_DIR / name)
        from kokoro_onnx import Kokoro
        _kokoro = Kokoro(str(KOKORO_DIR / "kokoro-v1.0.int8.onnx"), str(KOKORO_DIR / "voices-v1.0.bin"))
    return _kokoro


def _kokoro_turn(text: str, speaker: str) -> bytes:
    import numpy as np
    samples, rate = _get_kokoro().create(text, voice=PROVIDERS["kokoro"]["voices"][speaker], speed=SPEED, lang="en-us",
                                         sentence_pause=0.1, clause_pause=0.04)
    if rate != RATE:
        raise LLMError(f"Kokoro returned {rate} Hz audio; expected {RATE}.")
    return (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()


def _openai_turn(text: str, speaker: str, model: str) -> bytes:
    from openai import OpenAI
    import openai
    key = llm.get_key("openai")
    if not key:
        raise LLMError("Add your OpenAI API key first (top bar → AI model).")
    client = OpenAI(api_key=key, timeout=180, max_retries=1)
    try:
        extra = {"instructions": PACE_NOTE} if "gpt-4o" in model else {"speed": 1.1}  # tts-1 models take speed only
        res = client.audio.speech.create(model=model, voice=PROVIDERS["openai"]["voices"][speaker], input=text,
                                         response_format="pcm", **extra)
    except openai.RateLimitError as exc:
        raise QuotaError(f"OpenAI voice limit reached: {exc}") from exc
    except openai.APIError as exc:
        raise LLMError(f"OpenAI voice failed: {getattr(exc, 'message', exc)}") from exc
    return res.content


def _gemini_chunk(turns: list[dict], names: dict, model: str) -> bytes:
    key = llm.get_key("gemini")
    if not key:
        raise LLMError("Add your Gemini API key first (top bar → AI model).")
    script = "\n".join(f"{names[t['speaker']]}: {t['text']}" for t in turns)
    voices = PROVIDERS["gemini"]["voices"]
    body = {"contents": [{"parts": [{"text": f"Read this podcast conversation aloud, warm and natural. {PACE_NOTE}\n" + script}]}],
            "generationConfig": {"responseModalities": ["AUDIO"], "speechConfig": {"multiSpeakerVoiceConfig": {
                "speakerVoiceConfigs": [{"speaker": names[s], "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voices[s]}}}
                                        for s in ("A", "B")]}}}}
    req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                                 data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "x-goog-api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=300) as res:
            data = json.loads(res.read())
    except urllib.error.HTTPError as exc:
        msg = exc.read().decode(errors="replace")[:300]
        if exc.code == 429:
            raise QuotaError(f"Gemini voice limit reached: {msg}") from exc
        raise LLMError(f"Gemini voice failed ({exc.code}): {msg}") from exc
    except urllib.error.URLError as exc:
        raise LLMError(f"Couldn't reach Gemini ({exc.reason}).") from exc
    try:
        return base64.b64decode(data["candidates"][0]["content"]["parts"][0]["inlineData"]["data"])
    except (KeyError, IndexError) as exc:
        raise LLMError("Gemini returned no audio. Try again.") from exc


def _gemini_chunks(turns: list[dict]) -> list[list[dict]]:
    chunks, cur, size = [], [], 0
    for t in turns:
        if cur and size + len(t["text"]) > GEMINI_CHUNK_CHARS:
            chunks.append(cur)
            cur, size = [], 0
        cur.append(t)
        size += len(t["text"])
    return chunks + ([cur] if cur else [])


# ---------- render ----------
def encode_mp3(pcm: bytes) -> bytes:
    import av
    import numpy as np
    buf = io.BytesIO()
    with av.open(buf, "w", format="mp3") as out:
        stream = out.add_stream("libmp3lame", rate=RATE, layout="mono")
        stream.bit_rate = 64000
        samples = np.frombuffer(pcm, dtype="<i2")
        step = 1152 * 20
        for i in range(0, len(samples), step):
            frame = av.AudioFrame.from_ndarray(samples[i:i + step].reshape(1, -1), format="s16", layout="mono")
            frame.sample_rate = RATE
            for packet in stream.encode(frame):
                out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)
    return buf.getvalue()


def render(turns: list[dict], names: dict | None = None, on_progress=None) -> tuple[bytes, float]:
    """turns: [{"speaker": "A"|"B", "text": str}]. names: {"A": "Maya", "B": "Leo"} (Gemini needs them).
    on_progress(text) gets short status lines. Returns (mp3 bytes, duration in seconds)."""
    from . import stt
    stt.need_audio("av", "numpy")  # MP3 encoding, any voice engine
    say = on_progress or (lambda _text: None)
    turns = [{"speaker": "B" if t.get("speaker") == "B" else "A", "text": t["text"].strip()} for t in turns if t.get("text", "").strip()]
    if not turns:
        raise LLMError("The script is empty.")
    names = names or {"A": "Host A", "B": "Host B"}
    provider, model = choice()
    pause = b"\x00\x00" * int(RATE * PAUSE_S)
    if not _lock.acquire(blocking=False):
        say("Waiting for another podcast to finish recording…")
        _lock.acquire()
    try:
        parts = []
        if provider == "gemini":
            chunks = _gemini_chunks(turns)
            for i, c in enumerate(chunks, 1):
                say(f"Recording part {i} of {len(chunks)} with Gemini…")
                parts.append(_gemini_chunk(c, names, model))
        else:
            if provider == "kokoro" and _kokoro is None and not all((KOKORO_DIR / n).is_file() for n in KOKORO_FILES):
                say("Downloading the voices (first time only, ~120 MB)…")
                _get_kokoro()
            for i, t in enumerate(turns, 1):
                say(f"Recording line {i} of {len(turns)}…")
                parts.append(_openai_turn(t["text"], t["speaker"], model) if provider == "openai"
                             else _kokoro_turn(t["text"], t["speaker"]))
    finally:
        _lock.release()
    say("Making the MP3…")
    pcm = pause.join(parts)
    return encode_mp3(pcm), len(pcm) / 2 / RATE
