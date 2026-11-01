"""Local speech-to-text for podcast episodes and uploaded audio (faster-whisper, CPU, runs on this computer).

A source with audio gets `meta.stt` = queued | running | done | error (+ `meta.stt_error`). One worker thread
transcribes one file at a time; the transcript replaces the source content with `### [m:ss]` markers every ~60 s,
so chat and Studio can cite times. Podcast show notes stay on top under "## Show notes".

    stt.queue(source_id)                  # from the UI ("Transcribe") or an audio upload
"""
import importlib.util
import logging
import os
import queue as q
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

from . import db
from . import feeds

log = logging.getLogger("cueline.stt")

MODEL = os.environ.get("CUELINE_STT_MODEL", "base")  # ~150 MB, downloaded once into the Hugging Face cache
MAX_AUDIO_BYTES = 400 * 1024 * 1024
MAX_SECONDS = 4 * 3600
MARK_EVERY = 60  # seconds between ### [m:ss] markers
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav", ".ogg", ".opus", ".flac", ".webm", ".aac"}
AUDIO_DIR = db.DATA_DIR / "audio"  # uploaded audio waits here until it is transcribed, then is deleted

_jobs: q.Queue = q.Queue()
_model = None
_model_lock = threading.Lock()
on_done = None  # set by app: refresh the source guide after a transcript lands (= watch.on_source_added)
AUDIO_HINT = ("This needs Cueline's audio add-on (speech-to-text and podcast voices on this computer). "
              "Install it with: uv tool install --force 'cueline[audio] @ git+https://github.com/Helios-b/cueline' "
              "(from a checkout: uv run --extra audio cueline), then restart Cueline.")


def need_audio(*modules: str):
    """Raise a clear ValueError when the optional audio packages (the [audio] extra) aren't installed."""
    if any(importlib.util.find_spec(m) is None for m in modules):
        raise ValueError(AUDIO_HINT)


def _stamp(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def _get_model():
    global _model
    with _model_lock:
        if _model is None:
            from faster_whisper import WhisperModel
            _model = WhisperModel(MODEL, device="cpu", compute_type="int8", cpu_threads=max(1, (os.cpu_count() or 2) // 2))
        return _model


def transcribe(path: str) -> tuple[str, float]:
    """Returns (markdown with ### [m:ss] sections, duration in seconds)."""
    segments, info = _get_model().transcribe(path, vad_filter=True, beam_size=1)
    if info.duration > MAX_SECONDS:
        raise ValueError("That episode is longer than 4 hours.")
    parts, block, block_start = [], [], None
    for seg in segments:
        if block_start is None:
            block_start = seg.start
        block.append(seg.text.strip())
        if seg.end - block_start >= MARK_EVERY:
            parts.append(f"### [{_stamp(block_start)}]\n{' '.join(block)}")
            block, block_start = [], None
    if block:
        parts.append(f"### [{_stamp(block_start)}]\n{' '.join(block)}")
    if not parts:
        raise ValueError("No speech found in the audio.")
    return "\n\n".join(parts), info.duration


def download(url: str, dest: Path) -> None:
    """Stream a public audio URL to dest (same private-address checks as feeds, bigger cap)."""
    feeds._check_public(url)
    req = urllib.request.Request(url, headers={"User-Agent": feeds.UA})
    try:
        with feeds._opener.open(req, timeout=60) as res, open(dest, "wb") as out:
            total = 0
            while chunk := res.read(1 << 20):
                total += len(chunk)
                if total > MAX_AUDIO_BYTES:
                    raise ValueError("That audio file is larger than 400 MB.")
                out.write(chunk)
    except urllib.error.HTTPError as exc:
        raise ValueError(f"The audio host answered {exc.code} {exc.reason}.") from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"Couldn't download the audio ({exc.reason}).") from exc


def can_transcribe(src: dict) -> bool:
    meta = src.get("meta") or {}
    return bool(meta.get("audio") or meta.get("audio_file"))


def _set(src_id: str, **meta_changes):
    src = db.one("SELECT meta FROM sources WHERE id = ?", (src_id,))
    if src:
        db.update("sources", src_id, meta={**(src["meta"] or {}), **meta_changes})


def queue(src_id: str) -> dict:
    src = db.one("SELECT * FROM sources WHERE id = ?", (src_id,))
    if not src or not can_transcribe(src):
        raise ValueError("This source has no audio to transcribe.")
    need_audio("faster_whisper")
    if (src["meta"] or {}).get("stt") in ("queued", "running"):
        return src
    _set(src_id, stt="queued", stt_error=None)
    _jobs.put(src_id)
    return db.one("SELECT * FROM sources WHERE id = ?", (src_id,))


def _run(src_id: str):
    src = db.one("SELECT * FROM sources WHERE id = ?", (src_id,))
    if not src:
        return
    meta = src["meta"] or {}
    _set(src_id, stt="running")
    tmp = None
    try:
        if meta.get("audio_file"):
            path = AUDIO_DIR / Path(meta["audio_file"]).name
        else:
            fd, tmp = tempfile.mkstemp(suffix=".audio", dir=db.DATA_DIR)
            os.close(fd)
            download(meta["audio"], Path(tmp))
            path = Path(tmp)
        text, duration = transcribe(str(path))
        old = src["content"] or ""
        notes = "" if meta.get("audio_file") else (
            old.split("## Transcript")[0].replace("## Show notes", "", 1) if "## Transcript" in old else old).strip()
        content = f"## Show notes\n{notes}\n\n## Transcript\n\n{text}" if notes else text
        db.update("sources", src_id, content=content, word_count=len(content.split()),
                  meta={**meta, "stt": "done", "stt_error": None, "stt_model": MODEL, "duration_s": int(duration)})
        if meta.get("audio_file"):
            path.unlink(missing_ok=True)
            db.update("sources", src_id, meta={**db.one("SELECT meta FROM sources WHERE id = ?", (src_id,))["meta"],
                                               "audio_file": None})
        if on_done:
            on_done(src["notebook_id"], src_id)
    except Exception as exc:  # shown on the source; "Transcribe" retries
        log.warning("transcribe %s failed: %s", src_id, exc)
        _set(src_id, stt="error", stt_error=str(exc)[:300])
    finally:
        if tmp:
            Path(tmp).unlink(missing_ok=True)


def _worker():
    while True:
        src_id = _jobs.get()
        try:
            _run(src_id)
        except Exception as exc:
            log.warning("stt worker: %s", exc)


def start():
    """Start the worker and requeue jobs interrupted by a restart."""
    threading.Thread(target=_worker, daemon=True, name="stt").start()
    for src in db.query("SELECT id FROM sources WHERE json_extract(meta, '$.stt') IN ('queued', 'running')"):
        _jobs.put(src["id"])
